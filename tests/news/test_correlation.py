from __future__ import annotations

from app.core.models import NewsCategory, NewsDirection
from app.news.correlation import (
    CorroborationLevel,
    assess_corroboration,
    group_related_items,
)
from app.news.parser import ParsedNewsItem


def _item(*, source, tier, url, domain, content_hash="h", category=NewsCategory.REGULATORY,
          entities=("BTCUSDT",), published_ts_ms=1000):
    return ParsedNewsItem(
        source_name=source, tier=tier, canonical_url=url, domain=domain,
        published_ts_ms=published_ts_ms, fetched_ts_ms=published_ts_ms, receipt_ts_ms=published_ts_ms,
        category=category, direction=NewsDirection.UNKNOWN, entities=entities,
        content_hash=content_hash, raw_text="text",
    )


# ---------------------------------------------------------------------------
# Tier-1-alone sufficiency
# ---------------------------------------------------------------------------


def test_single_tier1_source_is_sufficient():
    group = [_item(source="sec_press", tier=1, url="https://sec.gov/1", domain="sec.gov", content_hash="h1")]
    result = assess_corroboration(group)
    assert result.level == CorroborationLevel.TIER1_VERIFIED
    assert result.can_support_high_or_critical is True


def test_tier1_sufficient_even_alongside_unrelated_tier3():
    group = [
        _item(source="binance_announcements", tier=1, url="https://binance.com/1", domain="binance.com", content_hash="h1"),
        _item(source="gdelt", tier=3, url="https://gdelt.org/1", domain="gdelt.org", content_hash="h2"),
    ]
    result = assess_corroboration(group)
    assert result.level == CorroborationLevel.TIER1_VERIFIED
    assert result.can_support_high_or_critical is True


# ---------------------------------------------------------------------------
# Two independent Tier-2 sources
# ---------------------------------------------------------------------------


def test_two_independent_tier2_sources_sufficient():
    group = [
        _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h1"),
        _item(source="the_block", tier=2, url="https://theblock.co/1", domain="theblock.co", content_hash="h2"),
    ]
    result = assess_corroboration(group)
    assert result.level == CorroborationLevel.TIER2_CORROBORATED
    assert result.can_support_high_or_critical is True


def test_single_tier2_source_insufficient():
    group = [_item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h1")]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False
    assert result.level == CorroborationLevel.TIER3_ONLY


def test_two_tier2_same_domain_different_url_not_independent():
    """Two articles on the SAME outlet are not independent
    corroboration -- same domain fails the independence test even with
    distinct URLs."""
    group = [
        _item(source="coindesk", tier=2, url="https://coindesk.com/article-a", domain="coindesk.com", content_hash="h1"),
        _item(source="coindesk", tier=2, url="https://coindesk.com/article-b", domain="coindesk.com", content_hash="h2"),
    ]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False


def test_two_tier2_same_url_different_domain_impossible_but_not_independent():
    """Edge case: identical canonical_url (would normally have been
    deduped already) must not count as independent even if domain
    parsing somehow differed."""
    group = [
        _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h1"),
        _item(source="the_block", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h2"),
    ]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False


def test_three_tier2_sources_two_independent_among_them_sufficient():
    group = [
        _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h1"),
        _item(source="coindesk", tier=2, url="https://coindesk.com/2", domain="coindesk.com", content_hash="h2"),
        _item(source="the_block", tier=2, url="https://theblock.co/1", domain="theblock.co", content_hash="h3"),
    ]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is True
    assert result.level == CorroborationLevel.TIER2_CORROBORATED


# ---------------------------------------------------------------------------
# Tier-3 hard ceiling: NEVER HIGH/CRITICAL, regardless of count
# ---------------------------------------------------------------------------


def test_single_tier3_source_insufficient():
    group = [_item(source="gdelt", tier=3, url="https://gdelt.org/1", domain="gdelt.org", content_hash="h1")]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False
    assert result.level == CorroborationLevel.TIER3_ONLY


def test_many_independent_tier3_sources_still_insufficient():
    """The hard ceiling: even FIVE independent Tier-3 sources must
    never clear HIGH/CRITICAL. This is the specific case the spec calls
    out: 'A single Tier-3 source can NEVER produce HIGH or CRITICAL
    severity' generalizes to 'no number of Tier-3 sources can'."""
    group = [
        _item(source="gdelt", tier=3, url=f"https://site{i}.com/1", domain=f"site{i}.com", content_hash=f"h{i}")
        for i in range(5)
    ]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False
    assert result.level == CorroborationLevel.TIER3_ONLY


def test_tier3_plus_single_tier2_still_insufficient():
    """Tier-3 corroboration cannot combine with a single (non-doubled)
    Tier-2 source to clear the ceiling -- still need genuinely two
    independent Tier-2 (or one Tier-1)."""
    group = [
        _item(source="coindesk", tier=2, url="https://coindesk.com/1", domain="coindesk.com", content_hash="h1"),
        _item(source="gdelt", tier=3, url="https://gdelt.org/1", domain="gdelt.org", content_hash="h2"),
    ]
    result = assess_corroboration(group)
    assert result.can_support_high_or_critical is False


def test_empty_group_is_none_level():
    result = assess_corroboration([])
    assert result.level == CorroborationLevel.NONE
    assert result.can_support_high_or_critical is False


# ---------------------------------------------------------------------------
# group_related_items
# ---------------------------------------------------------------------------


def test_group_related_items_groups_same_category_overlapping_entities_close_time():
    items = [
        _item(source="a", tier=2, url="https://a.com/1", domain="a.com", content_hash="h1",
              category=NewsCategory.HACK, entities=("BTCUSDT",), published_ts_ms=1_000_000),
        _item(source="b", tier=2, url="https://b.com/1", domain="b.com", content_hash="h2",
              category=NewsCategory.HACK, entities=("BTCUSDT",), published_ts_ms=1_000_000 + 600_000),
    ]
    groups = group_related_items(items, time_window_ms=3_600_000)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_group_related_items_separates_different_categories():
    items = [
        _item(source="a", tier=2, url="https://a.com/1", domain="a.com", content_hash="h1",
              category=NewsCategory.HACK, entities=("BTCUSDT",)),
        _item(source="b", tier=2, url="https://b.com/1", domain="b.com", content_hash="h2",
              category=NewsCategory.ETF, entities=("BTCUSDT",)),
    ]
    groups = group_related_items(items)
    assert len(groups) == 2


def test_group_related_items_separates_non_overlapping_entities():
    items = [
        _item(source="a", tier=2, url="https://a.com/1", domain="a.com", content_hash="h1", entities=("BTCUSDT",)),
        _item(source="b", tier=2, url="https://b.com/1", domain="b.com", content_hash="h2", entities=("ETHUSDT",)),
    ]
    groups = group_related_items(items)
    assert len(groups) == 2


def test_group_related_items_separates_items_outside_time_window():
    items = [
        _item(source="a", tier=2, url="https://a.com/1", domain="a.com", content_hash="h1", published_ts_ms=0),
        _item(source="b", tier=2, url="https://b.com/1", domain="b.com", content_hash="h2", published_ts_ms=10_000_000),
    ]
    groups = group_related_items(items, time_window_ms=3_600_000)
    assert len(groups) == 2


def test_group_related_items_market_wide_entities_overlap():
    items = [
        _item(source="a", tier=1, url="https://a.com/1", domain="a.com", content_hash="h1",
              category=NewsCategory.MACRO, entities=("MARKET",)),
        _item(source="b", tier=2, url="https://b.com/1", domain="b.com", content_hash="h2",
              category=NewsCategory.MACRO, entities=("MARKET",)),
    ]
    groups = group_related_items(items)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_group_related_items_single_item_batch():
    items = [_item(source="a", tier=1, url="https://a.com/1", domain="a.com", content_hash="h1")]
    groups = group_related_items(items)
    assert len(groups) == 1
    assert len(groups[0]) == 1
