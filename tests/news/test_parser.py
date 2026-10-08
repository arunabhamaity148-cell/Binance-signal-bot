from __future__ import annotations

from app.core.models import NewsCategory, NewsDirection
from app.news.parser import (
    RawFeedItem,
    canonicalize_url,
    classify_category,
    classify_direction,
    compute_content_hash,
    extract_domain,
    extract_entities,
    parse_raw_item,
)


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------


def test_canonicalize_url_strips_tracking_params():
    url = "https://example.com/article?utm_source=twitter&utm_campaign=x&id=5"
    result = canonicalize_url(url)
    assert "utm_source" not in result
    assert "utm_campaign" not in result
    assert "id=5" in result


def test_canonicalize_url_strips_fragment():
    url = "https://example.com/article#section2"
    assert "#" not in canonicalize_url(url)


def test_canonicalize_url_lowercases_scheme_and_host():
    url = "HTTPS://Example.COM/Article"
    result = canonicalize_url(url)
    assert result.startswith("https://example.com")


def test_canonicalize_url_strips_trailing_slash():
    a = canonicalize_url("https://example.com/article/")
    b = canonicalize_url("https://example.com/article")
    assert a == b


def test_canonicalize_url_equal_for_tracking_variants():
    a = canonicalize_url("https://example.com/story?utm_source=a&ref=x")
    b = canonicalize_url("https://example.com/story?fbclid=zzz")
    assert a == b


def test_canonicalize_url_preserves_distinct_paths():
    a = canonicalize_url("https://example.com/story-a")
    b = canonicalize_url("https://example.com/story-b")
    assert a != b


def test_extract_domain():
    assert extract_domain("https://www.CoinDesk.com/markets/x") == "www.coindesk.com"


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def test_compute_content_hash_deterministic():
    h1 = compute_content_hash("Title", "Summary text")
    h2 = compute_content_hash("Title", "Summary text")
    assert h1 == h2


def test_compute_content_hash_case_and_whitespace_insensitive():
    h1 = compute_content_hash("Big   News", "Something happened")
    h2 = compute_content_hash("big news", "something happened")
    assert h1 == h2


def test_compute_content_hash_differs_for_different_content():
    h1 = compute_content_hash("Title A", "Body A")
    h2 = compute_content_hash("Title B", "Body B")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


def test_extract_entities_matches_ticker():
    assert "BTCUSDT" in extract_entities("BTC surges past resistance")


def test_extract_entities_matches_common_name():
    assert "BTCUSDT" in extract_entities("Bitcoin surges past resistance")


def test_extract_entities_matches_multiple_assets():
    entities = extract_entities("ETH and SOL both rallied today")
    assert "ETHUSDT" in entities
    assert "SOLUSDT" in entities


def test_extract_entities_no_match_returns_empty():
    assert extract_entities("The weather was nice today") == ()


def test_extract_entities_whole_word_only_no_substring_false_positive():
    """'ATOM' should not match inside an unrelated longer word."""
    entities = extract_entities("The diplomat omitted comment")
    assert "ATOMUSDT" not in entities


def test_extract_entities_market_wide_hint():
    entities = extract_entities("The Fed signaled a rate cut")
    assert "MARKET" in entities


def test_extract_entities_sec_is_market_wide():
    entities = extract_entities("The SEC filed a new rule")
    assert "MARKET" in entities


def test_extract_entities_no_market_hint_omits_market():
    entities = extract_entities("Bitcoin price moved higher")
    assert "MARKET" not in entities


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------


def test_classify_category_regulatory():
    assert classify_category("SEC files enforcement action against exchange") == NewsCategory.REGULATORY


def test_classify_category_hack():
    assert classify_category("Protocol suffers hack, funds drained") == NewsCategory.HACK


def test_classify_category_listing():
    assert classify_category("Binance lists new token") == NewsCategory.LISTING


def test_classify_category_delisting():
    assert classify_category("Exchange announces it will delist several pairs") == NewsCategory.DELISTING


def test_classify_category_etf():
    assert classify_category("Spot ETF sees record inflows") == NewsCategory.ETF


def test_classify_category_macro():
    assert classify_category("Fed holds interest rates steady") == NewsCategory.MACRO


def test_classify_category_liquidation():
    assert classify_category("Long squeeze triggers liquidation cascade") == NewsCategory.LIQUIDATION


def test_classify_category_outage():
    assert classify_category("Exchange reports service outage") == NewsCategory.OUTAGE


def test_classify_category_falls_back_to_other():
    assert classify_category("A completely unrelated sentence about gardening") == NewsCategory.OTHER


# ---------------------------------------------------------------------------
# Direction classification
# ---------------------------------------------------------------------------


def test_classify_direction_bullish():
    assert classify_direction("Price rallies on strong adoption news") == NewsDirection.BULLISH


def test_classify_direction_bearish():
    assert classify_direction("Market crashes after exchange hack") == NewsDirection.BEARISH


def test_classify_direction_unknown_when_neither():
    assert classify_direction("Analysts discuss market structure") == NewsDirection.UNKNOWN


def test_classify_direction_unknown_when_ambiguous_both():
    """Both a bullish and bearish term present -> conservative UNKNOWN,
    never a forced guess. Uses exact keyword matches from both
    _BULLISH_TERMS ('rally') and _BEARISH_TERMS ('lawsuit') so the
    ambiguity is genuine against the actual keyword lists, not an
    assumption about words that merely sound bullish/bearish."""
    text = "Rally fades as new lawsuit is filed"
    assert classify_direction(text) == NewsDirection.UNKNOWN


def test_classify_direction_never_defaults_to_neutral():
    """NEUTRAL is a positive claim; UNKNOWN is the honest default."""
    assert classify_direction("") != NewsDirection.NEUTRAL
    assert classify_direction("") == NewsDirection.UNKNOWN


# ---------------------------------------------------------------------------
# Full parse_raw_item
# ---------------------------------------------------------------------------


def test_parse_raw_item_full_pipeline():
    raw = RawFeedItem(
        source_name="coindesk", tier=2, title="Bitcoin rallies on ETF approval",
        summary="Spot ETF inflows surge as Bitcoin breaks resistance",
        url="https://coindesk.com/markets/btc-rally?utm_source=rss",
        published_ts_ms=1000, fetched_ts_ms=2000,
    )
    parsed = parse_raw_item(raw, receipt_ts_ms=2500)
    assert parsed.source_name == "coindesk"
    assert parsed.tier == 2
    assert "utm_source" not in parsed.canonical_url
    assert parsed.domain == "coindesk.com"
    assert "BTCUSDT" in parsed.entities
    assert parsed.category == NewsCategory.ETF
    assert parsed.direction == NewsDirection.BULLISH
    assert parsed.receipt_ts_ms == 2500
    assert len(parsed.content_hash) == 64  # sha256 hex digest length


def test_parse_raw_item_preserves_timestamps_distinctly():
    raw = RawFeedItem(
        source_name="x", tier=1, title="T", summary="S", url="https://x.com/1",
        published_ts_ms=100, fetched_ts_ms=200,
    )
    parsed = parse_raw_item(raw, receipt_ts_ms=300)
    assert parsed.published_ts_ms == 100
    assert parsed.fetched_ts_ms == 200
    assert parsed.receipt_ts_ms == 300
