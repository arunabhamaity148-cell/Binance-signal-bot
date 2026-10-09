from __future__ import annotations

from app.config import load_all
from app.core.models import NewsCategory, NewsDirection, NewsSeverity
from app.news.engine import NewsEngine
from app.news.parser import ParsedNewsItem

NEWS_CFG = load_all().news_sources


def _item(*, source, tier, url, domain, content_hash, category, entities=("BTCUSDT",), published_ts_ms=1_000_000):
    return ParsedNewsItem(
        source_name=source, tier=tier, canonical_url=url, domain=domain,
        published_ts_ms=published_ts_ms, fetched_ts_ms=published_ts_ms, receipt_ts_ms=published_ts_ms,
        category=category, direction=NewsDirection.BEARISH, entities=entities,
        content_hash=content_hash, raw_text="text",
    )


def test_ingest_batch_deduplicates():
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com",
                content_hash="h1", category=NewsCategory.HACK)
    new1 = engine.ingest_batch([item])
    new2 = engine.ingest_batch([item])
    assert len(new1) == 1
    assert len(new2) == 0


def test_build_news_state_produces_event_for_healthy_corroborated_category():
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="binance_announcements", tier=1, url="https://binance.com/1", domain="binance.com",
                content_hash="h1", category=NewsCategory.HACK, published_ts_ms=1_000_000)
    engine.ingest_batch([item])
    engine.record_source_health("binance_announcements", True)
    state = engine.build_news_state(as_of_ts_ms=1_000_100)
    assert len(state.active_events) == 1
    assert state.active_events[0].category == NewsCategory.HACK


def test_build_news_state_excludes_decayed_events():
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="binance_announcements", tier=1, url="https://binance.com/1", domain="binance.com",
                content_hash="h1", category=NewsCategory.OUTAGE, published_ts_ms=0)  # short half-life category
    engine.ingest_batch([item])
    engine.record_source_health("binance_announcements", True)
    half_life_hours = NEWS_CFG["category_half_life_hours"]["outage"]
    far_future = int(half_life_hours * 3_600_000 * 10)  # well past decay
    state = engine.build_news_state(as_of_ts_ms=far_future)
    assert len(state.active_events) == 0


# ---------------------------------------------------------------------------
# Per-category fail-closed, never system-wide freeze
# ---------------------------------------------------------------------------


def test_unhealthy_category_excluded_but_other_categories_unaffected():
    """THE core requirement: when a category's sources are
    collectively unhealthy, that category's events are excluded, but
    events in OTHER categories still appear normally -- proving the
    system never does an all-or-nothing freeze."""
    engine = NewsEngine(NEWS_CFG)

    hack_item = _item(source="binance_announcements", tier=1, url="https://binance.com/hack1",
                      domain="binance.com", content_hash="h1", category=NewsCategory.HACK,
                      published_ts_ms=1_000_000)
    etf_item = _item(source="coindesk", tier=2, url="https://coindesk.com/etf1", domain="coindesk.com",
                     content_hash="h2", category=NewsCategory.ETF, published_ts_ms=1_000_000)
    etf_item2 = _item(source="the_block", tier=2, url="https://theblock.co/etf1", domain="theblock.co",
                      content_hash="h3", category=NewsCategory.ETF, published_ts_ms=1_000_000)

    engine.ingest_batch([hack_item, etf_item, etf_item2])

    # HACK's only source is unhealthy; ETF's sources are healthy.
    engine.record_source_health("binance_announcements", False)
    engine.record_source_health("coindesk", True)
    engine.record_source_health("the_block", True)

    state = engine.build_news_state(as_of_ts_ms=1_000_100)

    assert NewsCategory.HACK in state.unhealthy_categories
    assert NewsCategory.ETF not in state.unhealthy_categories

    categories_in_events = {e.category for e in state.active_events}
    assert NewsCategory.HACK not in categories_in_events
    assert NewsCategory.ETF in categories_in_events, "ETF events must still appear despite HACK being unhealthy"


def test_all_categories_unhealthy_still_returns_a_valid_newsstate_not_a_crash():
    """Even in the worst case (every category unhealthy), the engine
    must return a well-formed (empty) NewsState, never raise or hang —
    'do not freeze the whole system' means the system keeps running,
    just with no active news events."""
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com",
                content_hash="h1", category=NewsCategory.HACK)
    engine.ingest_batch([item])
    engine.record_source_health("coindesk", False)

    state = engine.build_news_state(as_of_ts_ms=1_000_100)
    assert state.active_events == ()
    assert NewsCategory.HACK in state.unhealthy_categories


def test_category_with_no_sources_at_all_is_treated_as_healthy_by_default():
    """A category nobody has reported items for yet has nothing to
    fail closed on -- it should not be preemptively marked unhealthy."""
    engine = NewsEngine(NEWS_CFG)
    state = engine.build_news_state(as_of_ts_ms=1000)
    assert NewsCategory.LIQUIDATION not in state.unhealthy_categories


def test_category_healthy_if_at_least_one_contributing_source_is_healthy():
    engine = NewsEngine(NEWS_CFG)
    item1 = _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com",
                  content_hash="h1", category=NewsCategory.HACK, published_ts_ms=1_000_000)
    item2 = _item(source="the_block", tier=2, url="https://theblock.co/1", domain="theblock.co",
                  content_hash="h2", category=NewsCategory.HACK, published_ts_ms=1_000_000)
    engine.ingest_batch([item1, item2])
    engine.record_source_health("coindesk", False)
    engine.record_source_health("the_block", True)  # at least one healthy source

    state = engine.build_news_state(as_of_ts_ms=1_000_100)
    assert NewsCategory.HACK not in state.unhealthy_categories


def test_build_news_state_is_idempotent_given_same_inputs():
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="binance_announcements", tier=1, url="https://binance.com/1", domain="binance.com",
                content_hash="h1", category=NewsCategory.HACK, published_ts_ms=1_000_000)
    engine.ingest_batch([item])
    engine.record_source_health("binance_announcements", True)

    state1 = engine.build_news_state(as_of_ts_ms=1_000_100)
    state2 = engine.build_news_state(as_of_ts_ms=1_000_100)
    assert len(state1.active_events) == len(state2.active_events) == 1


def test_max_severity_for_symbol_reflects_active_events():
    engine = NewsEngine(NEWS_CFG)
    item = _item(source="binance_announcements", tier=1, url="https://binance.com/1", domain="binance.com",
                content_hash="h1", category=NewsCategory.HACK, entities=("BTCUSDT",),
                published_ts_ms=1_000_000)
    engine.ingest_batch([item])
    engine.record_source_health("binance_announcements", True)
    state = engine.build_news_state(as_of_ts_ms=1_000_100)
    severity = state.max_severity_for("BTCUSDT")
    assert severity != NewsSeverity.LOW or len(state.active_events) == 0


# ---------------------------------------------------------------------------
# run_collection_cycle: one source's failure must not abort the cycle
# ---------------------------------------------------------------------------


import httpx
import logging
import pytest

from app.news.collectors import NewsCollector, RetryConfig, SourceConfig, SourceFormat
from app.news.engine import run_collection_cycle

_FAST_RETRY = RetryConfig(max_attempts=2, base_backoff_ms=1, jitter_ms=1, respect_retry_after_header=True)

_RSS_OK = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Bitcoin story</title><link>https://good.example.com/1</link><summary>text</summary></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_run_collection_cycle_one_source_failure_does_not_abort_others():
    def handler(request: httpx.Request) -> httpx.Response:
        if "bad" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=_RSS_OK)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    engine = NewsEngine(NEWS_CFG)

    sources = [
        SourceConfig(name="bad_source", url="https://bad.example.com/feed", tier=2,
                    format=SourceFormat.RSS, timeout_s=5.0, credibility_weight=0.7),
        SourceConfig(name="good_source", url="https://good.example.com/feed", tier=2,
                    format=SourceFormat.RSS, timeout_s=5.0, credibility_weight=0.7),
    ]

    try:
        await run_collection_cycle(collector, engine, sources, receipt_ts_ms=1_000_000)
    finally:
        await collector.close()

    # The good source's item must have been ingested despite the bad
    # source's total failure.
    assert len(engine._known_items) == 1
    assert engine._known_items[0].source_name == "good_source"


@pytest.mark.asyncio
async def test_run_collection_cycle_skips_disabled_source_without_failure_warning(caplog):
    caplog.set_level(logging.INFO, logger="app.news.engine")
    class FailIfCalledCollector:
        async def fetch_source(self, source):
            raise AssertionError("disabled source must not be fetched")

        def health_for(self, source_name):
            raise AssertionError("disabled source must not update health")

    source = SourceConfig(name="disabled_source", url="https://disabled.example.com/feed", tier=2,
                          format=SourceFormat.RSS, timeout_s=5.0, credibility_weight=0.7, enabled=False)
    await run_collection_cycle(FailIfCalledCollector(), NewsEngine(NEWS_CFG), [source], receipt_ts_ms=1_000_000)
    assert not any(record.getMessage() == "news_source_failed" for record in caplog.records)
    assert any(record.getMessage() == "news_source_disabled" for record in caplog.records)
