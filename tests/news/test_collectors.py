from __future__ import annotations

import httpx
import pytest

from app.config import load_all
from app.core.errors import NewsSourceUnavailableError
from app.news.collectors import (
    NewsCollector,
    RetryConfig,
    SourceConfig,
    SourceFormat,
    SourceHealthState,
    assess_source_health,
    build_retry_config,
    build_source_configs,
)

NEWS_CFG = load_all().news_sources

_FAST_RETRY = RetryConfig(max_attempts=3, base_backoff_ms=1, jitter_ms=1, respect_retry_after_header=True)

_SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Bitcoin rallies on ETF news</title>
<link>https://example.com/story1</link>
<summary>Spot ETF inflows surge</summary>
</item>
</channel></rss>"""


def _source(url="https://example.com/feed", fmt=SourceFormat.RSS, timeout_s=5.0):
    return SourceConfig(name="test_source", url=url, tier=2, format=fmt, timeout_s=timeout_s, credibility_weight=0.7)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_build_source_configs_includes_all_tiers():
    sources = build_source_configs(NEWS_CFG)
    names = {s.name for s in sources}
    assert "federal_reserve_press" in names
    assert "coindesk" in names
    assert "gdelt" in names


def test_build_source_configs_uses_concrete_sec_endpoint():
    """The deployed registry must not ship literal placeholder URLs."""
    sources = build_source_configs(NEWS_CFG)
    sec = next(s for s in sources if s.name == "sec_press")
    assert sec.url == "https://www.sec.gov/news/pressreleases.rss"


def test_build_retry_config_from_news_sources_yaml():
    retry = build_retry_config(NEWS_CFG)
    assert retry.max_attempts == NEWS_CFG["retry"]["max_attempts"]
    assert retry.respect_retry_after_header is True


# ---------------------------------------------------------------------------
# Source health state
# ---------------------------------------------------------------------------


def test_source_health_initial_state():
    h = SourceHealthState(source_name="x")
    assert h.total_attempts == 0
    assert h.success_rate == 0.0


def test_source_health_record_success():
    h = SourceHealthState(source_name="x")
    h.record_success(1000)
    assert h.consecutive_failures == 0
    assert h.success_rate == 1.0


def test_source_health_record_failure_increments_consecutive():
    h = SourceHealthState(source_name="x")
    h.record_failure(1000)
    h.record_failure(2000)
    assert h.consecutive_failures == 2


def test_source_health_success_resets_consecutive_failures():
    h = SourceHealthState(source_name="x")
    h.record_failure(1000)
    h.record_success(2000)
    assert h.consecutive_failures == 0


def test_source_health_success_rate_mixed():
    h = SourceHealthState(source_name="x")
    h.record_success(1000)
    h.record_failure(2000)
    assert h.success_rate == pytest.approx(0.5)


def test_assess_source_health_untested_source_is_healthy():
    h = SourceHealthState(source_name="x")
    assert assess_source_health(h) is True


def test_assess_source_health_good_rate_is_healthy():
    h = SourceHealthState(source_name="x")
    h.record_success(1000)
    h.record_success(2000)
    h.record_failure(3000)
    assert assess_source_health(h, min_success_rate=0.5) is True


def test_assess_source_health_poor_rate_is_unhealthy():
    h = SourceHealthState(source_name="x")
    h.record_failure(1000)
    h.record_failure(2000)
    h.record_success(3000)
    assert assess_source_health(h, min_success_rate=0.5) is False


# ---------------------------------------------------------------------------
# Fetching: success, retry, 429, TLS (via transport mocking, no real network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_source_success_parses_rss():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_SAMPLE_RSS)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source())
        assert len(items) == 1
        assert items[0].title == "Bitcoin rallies on ETF news"
        health = collector.health_for("test_source")
        assert health.total_successes == 1
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_retries_on_500_then_succeeds():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(500)
        return httpx.Response(200, text=_SAMPLE_RSS)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source())
        assert len(items) == 1
        assert call_count["n"] == 2
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_exhausts_retries_and_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        with pytest.raises(NewsSourceUnavailableError):
            await collector.fetch_source(_source())
        health = collector.health_for("test_source")
        assert health.total_successes == 0
        assert health.consecutive_failures == _FAST_RETRY.max_attempts
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_respects_429_retry_after():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, text=_SAMPLE_RSS)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source())
        assert len(items) == 1
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_429_exhausted_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.01"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        with pytest.raises(NewsSourceUnavailableError):
            await collector.fetch_source(_source())
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_skips_malformed_rss_entry_keeps_valid_ones():
    malformed_rss = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Valid Story</title><link>https://example.com/valid</link><summary>ok</summary></item>
<item><summary>missing title and link</summary></item>
</channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=malformed_rss)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source())
        assert len(items) == 1
        assert items[0].title == "Valid Story"
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_json_format():
    import json

    body = json.dumps({"items": [{"title": "JSON story", "url": "https://example.com/json1", "summary": "body"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source(fmt=SourceFormat.JSON))
        assert len(items) == 1
        assert items[0].title == "JSON story"
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_malformed_json_returns_empty_not_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{not valid json")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        items = await collector.fetch_source(_source(fmt=SourceFormat.JSON))
        assert items == []
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_fetch_source_timeout_retries_then_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    collector = NewsCollector(_FAST_RETRY, client=client)
    try:
        with pytest.raises(NewsSourceUnavailableError):
            await collector.fetch_source(_source())
    finally:
        await collector.close()


def test_collector_never_disables_tls_verification():
    """Static-style assertion: the NewsCollector's default client
    construction never passes verify=False. Complements
    scripts/scan_forbidden_calls.py's static scan of this file.

    Checks only actual CODE lines (via ast), not the module's own
    docstring prose -- an earlier version of this test did a raw
    substring search over the whole module source, which falsely
    failed because this module's docstring itself explains, in words,
    that TLS verification is never disabled (the phrase 'verify=False'
    appears there as a negative example, not as code)."""
    import ast
    import inspect

    import app.news.collectors as collectors_module

    source = inspect.getsource(collectors_module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "verify":
            if isinstance(node.value, ast.Constant) and node.value.value is False:
                pytest.fail(f"found verify=False at line {node.lineno}")
