from __future__ import annotations

from app.core.models import NewsCategory, NewsDirection, NewsSeverity
from app.news.correlation import CorroborationLevel, CorroborationResult, assess_corroboration
from app.news.credibility import SourceCredibility
from app.news.impact import (
    assess_impact,
    compute_confidence,
    compute_market_impact_score,
    compute_novelty_score,
    compute_severity,
)
from app.news.parser import ParsedNewsItem


def _item(category=NewsCategory.HACK, entities=("BTCUSDT",)):
    return ParsedNewsItem(
        source_name="binance_announcements", tier=1, canonical_url="https://binance.com/1",
        domain="binance.com", published_ts_ms=1000, fetched_ts_ms=1000, receipt_ts_ms=1000,
        category=category, direction=NewsDirection.BEARISH, entities=entities,
        content_hash="h1", raw_text="text",
    )


def _tier1_credibility():
    return SourceCredibility(source_name="binance_announcements", tier=1, credibility_weight=1.0)


def _tier3_credibility():
    return SourceCredibility(source_name="gdelt", tier=3, credibility_weight=0.3)


_CORROBORATED = CorroborationResult(
    level=CorroborationLevel.TIER1_VERIFIED, can_support_high_or_critical=True,
    contributing_sources=("binance_announcements",), reason="tier1",
)
_UNCORROBORATED = CorroborationResult(
    level=CorroborationLevel.TIER3_ONLY, can_support_high_or_critical=False,
    contributing_sources=("gdelt",), reason="tier3 only",
)


# ---------------------------------------------------------------------------
# Novelty
# ---------------------------------------------------------------------------


def test_novelty_first_seen_is_full():
    assert compute_novelty_score(_item(), is_first_seen=True) == 1.0


def test_novelty_re_reported_is_discounted():
    score = compute_novelty_score(_item(), is_first_seen=False)
    assert 0 < score < 1.0


# ---------------------------------------------------------------------------
# Market impact score
# ---------------------------------------------------------------------------


def test_market_impact_higher_for_hack_than_other():
    hack_score = compute_market_impact_score(NewsCategory.HACK, _tier1_credibility(), entity_count=1)
    other_score = compute_market_impact_score(NewsCategory.OTHER, _tier1_credibility(), entity_count=1)
    assert hack_score > other_score


def test_market_impact_scales_with_credibility():
    high_cred_score = compute_market_impact_score(NewsCategory.HACK, _tier1_credibility(), entity_count=1)
    low_cred_score = compute_market_impact_score(NewsCategory.HACK, _tier3_credibility(), entity_count=1)
    assert high_cred_score > low_cred_score


def test_market_impact_bounded_to_one():
    score = compute_market_impact_score(NewsCategory.HACK, _tier1_credibility(), entity_count=1)
    assert 0 <= score <= 1.0


def test_market_impact_more_specific_entities_score_higher():
    specific = compute_market_impact_score(NewsCategory.HACK, _tier1_credibility(), entity_count=1)
    broad = compute_market_impact_score(NewsCategory.HACK, _tier1_credibility(), entity_count=10)
    assert specific >= broad


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_confidence_tier1_corroborated_is_highest():
    conf = compute_confidence(_tier1_credibility(), _CORROBORATED)
    assert conf == 1.0


def test_confidence_uncorroborated_is_discounted():
    conf_corroborated = compute_confidence(_tier1_credibility(), _CORROBORATED)
    conf_uncorroborated = compute_confidence(_tier1_credibility(), _UNCORROBORATED)
    assert conf_uncorroborated < conf_corroborated


def test_confidence_bounded_to_one():
    assert compute_confidence(_tier1_credibility(), _CORROBORATED) <= 1.0


# ---------------------------------------------------------------------------
# Severity gated by corroboration -- THE critical rule
# ---------------------------------------------------------------------------


def test_severity_reaches_critical_when_corroborated_and_high_impact():
    severity = compute_severity(market_impact_score=0.95, corroboration=_CORROBORATED)
    assert severity == NewsSeverity.CRITICAL


def test_severity_capped_at_medium_when_not_corroborated_even_with_high_impact():
    """THE critical rule: a market_impact_score of 0.95 (which would be
    CRITICAL if corroborated) must be capped at MEDIUM when
    corroboration.can_support_high_or_critical is False. This is the
    gate that makes corroboration a hard constraint, not just one input
    among several."""
    severity = compute_severity(market_impact_score=0.95, corroboration=_UNCORROBORATED)
    assert severity == NewsSeverity.MEDIUM


def test_severity_never_exceeds_medium_for_uncorroborated_at_any_impact_level():
    for score in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        severity = compute_severity(market_impact_score=score, corroboration=_UNCORROBORATED)
        assert severity in (NewsSeverity.LOW, NewsSeverity.MEDIUM), (
            f"score={score} produced {severity}, exceeding the MEDIUM ceiling for uncorroborated news"
        )


def test_severity_low_impact_stays_low_even_when_corroborated():
    severity = compute_severity(market_impact_score=0.1, corroboration=_CORROBORATED)
    assert severity == NewsSeverity.LOW


def test_severity_medium_band_when_corroborated():
    severity = compute_severity(market_impact_score=0.5, corroboration=_CORROBORATED)
    assert severity == NewsSeverity.MEDIUM


def test_severity_high_band_when_corroborated():
    severity = compute_severity(market_impact_score=0.7, corroboration=_CORROBORATED)
    assert severity == NewsSeverity.HIGH


# ---------------------------------------------------------------------------
# Full assess_impact
# ---------------------------------------------------------------------------


def test_assess_impact_full_pipeline_corroborated():
    result = assess_impact(
        item=_item(), credibility=_tier1_credibility(), corroboration=_CORROBORATED, is_first_seen=True,
    )
    assert result.severity in (NewsSeverity.LOW, NewsSeverity.MEDIUM, NewsSeverity.HIGH, NewsSeverity.CRITICAL)
    assert 0 <= result.confidence <= 1.0
    assert 0 <= result.market_impact_score <= 1.0
    assert result.novelty_score == 1.0


def test_assess_impact_full_pipeline_uncorroborated_caps_severity():
    result = assess_impact(
        item=_item(), credibility=_tier1_credibility(), corroboration=_UNCORROBORATED, is_first_seen=True,
    )
    assert result.severity in (NewsSeverity.LOW, NewsSeverity.MEDIUM)
