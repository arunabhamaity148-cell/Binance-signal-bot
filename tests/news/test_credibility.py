from __future__ import annotations

import pytest

from app.config import load_all
from app.news.credibility import build_credibility_index, lookup_credibility

NEWS_CFG = load_all().news_sources


def test_build_credibility_index_includes_all_tiers():
    index = build_credibility_index(NEWS_CFG)
    assert "federal_reserve_press" in index
    assert "coindesk" in index
    assert "gdelt" in index


def test_tier1_sources_have_tier_1():
    index = build_credibility_index(NEWS_CFG)
    assert index["sec_press"].tier == 1


def test_tier2_sources_have_tier_2():
    index = build_credibility_index(NEWS_CFG)
    assert index["coindesk"].tier == 2


def test_tier3_sources_have_tier_3():
    index = build_credibility_index(NEWS_CFG)
    assert index["gdelt"].tier == 3


def test_credibility_weight_matches_config():
    index = build_credibility_index(NEWS_CFG)
    assert index["coindesk"].credibility_weight == 0.75
    assert index["cointelegraph"].credibility_weight == 0.65


def test_intra_tier_adjustment_cointelegraph_below_coindesk():
    """NEWS_REGISTRY.md: Cointelegraph weighted below CoinDesk/The Block
    within Tier 2 due to higher noise."""
    index = build_credibility_index(NEWS_CFG)
    assert index["cointelegraph"].credibility_weight < index["coindesk"].credibility_weight
    assert index["cointelegraph"].credibility_weight < index["the_block"].credibility_weight


def test_tier1_weighted_above_tier2():
    index = build_credibility_index(NEWS_CFG)
    assert index["sec_press"].credibility_weight > index["coindesk"].credibility_weight


def test_tier2_weighted_above_tier3():
    index = build_credibility_index(NEWS_CFG)
    assert index["coindesk"].credibility_weight > index["gdelt"].credibility_weight


def test_lookup_credibility_unknown_source_raises():
    index = build_credibility_index(NEWS_CFG)
    with pytest.raises(KeyError):
        lookup_credibility("not_a_real_source", index)


def test_lookup_credibility_returns_correct_entry():
    index = build_credibility_index(NEWS_CFG)
    result = lookup_credibility("binance_announcements", index)
    assert result.source_name == "binance_announcements"
    assert result.tier == 1
