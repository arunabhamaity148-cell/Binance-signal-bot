"""Impact scoring: novelty, market-impact, and confidence scoring, and
the final severity computation gated by the corroboration rule.

Per spec section 14's mandatory components: "novelty scoring",
"market-impact scoring", "confidence scoring", and "event severity".

SEVERITY IS GATED BY CORROBORATION, NOT COMPUTED INDEPENDENTLY: the
corroboration result from correlation.py determines the CEILING a
group's severity can reach (TIER3_ONLY -> MEDIUM max; TIER2_CORROBORATED
or TIER1_VERIFIED -> HIGH/CRITICAL possible). Within that ceiling, the
actual severity is driven by category weight, credibility, and asset
relevance (entity specificity). This two-stage design (ceiling from
corroboration, then a graded score within the ceiling) is what makes
the corroboration rule a genuine hard constraint rather than one input
among several that a high-enough score from other factors could
override.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import NewsCategory, NewsSeverity
from app.news.correlation import CorroborationResult
from app.news.credibility import SourceCredibility
from app.news.parser import ParsedNewsItem

# Category severity weight: how impactful this category of news
# typically is, all else equal. Class F (unvalidated) -- a starting
# hypothesis, not calibrated. Not present in CONFIG_SCHEMAS.md as a
# separate config key (the design doc only specifies category
# half-lives, not category severity weights), so this is defined here
# as a module-level constant rather than invented into the config
# schema unprompted; flagged in KNOWN_UNCERTAINTIES.md-style fashion
# via this comment for anyone auditing where it comes from.
_CATEGORY_SEVERITY_WEIGHT: dict[NewsCategory, float] = {
    NewsCategory.REGULATORY: 0.9,
    NewsCategory.HACK: 0.9,
    NewsCategory.DELISTING: 0.85,
    NewsCategory.LIQUIDATION: 0.8,
    NewsCategory.ETF: 0.75,
    NewsCategory.MACRO: 0.7,
    NewsCategory.OUTAGE: 0.65,
    NewsCategory.LISTING: 0.5,
    NewsCategory.OTHER: 0.3,
}

_SEVERITY_ORDER = (NewsSeverity.LOW, NewsSeverity.MEDIUM, NewsSeverity.HIGH, NewsSeverity.CRITICAL)


@dataclass(frozen=True)
class ImpactAssessment:
    severity: NewsSeverity
    confidence: float
    market_impact_score: float
    novelty_score: float


def compute_novelty_score(item: ParsedNewsItem, is_first_seen: bool) -> float:
    """1.0 for a genuinely first-seen story, decaying toward a floor
    for re-reported/aggregated coverage of an already-known story.
    Tier-3 aggregator volume (GDELT, Google News) is exactly the kind
    of re-reporting signal this is meant to down-weight per
    NEWS_REGISTRY.md: 'used for novelty-decay input, not as a fact
    source' -- so this function does not consume Tier-3 volume as a
    positive signal, only `is_first_seen` (determined by the caller via
    deduper.py's dedup store)."""
    return 1.0 if is_first_seen else 0.4


def compute_market_impact_score(
    category: NewsCategory, credibility: SourceCredibility, entity_count: int
) -> float:
    """Combines category severity weight, source credibility, and
    asset-relevance specificity (fewer entities = more specific = more
    directly relevant to any one of them) into a single 0-1 score."""
    category_weight = _CATEGORY_SEVERITY_WEIGHT.get(category, 0.3)
    specificity = 1.0 if 0 < entity_count <= 2 else (0.7 if entity_count > 2 else 0.5)
    return min(1.0, category_weight * credibility.credibility_weight * specificity)


def compute_confidence(credibility: SourceCredibility, corroboration: CorroborationResult) -> float:
    """Derived from credibility x corroboration state, per spec section
    14's 'confidence scoring: Derived from credibility x corroboration
    state' (CONFIG_SCHEMAS.md's news_sources.yaml evaluation table)."""
    corroboration_multiplier = {
        "TIER1_VERIFIED": 1.0,
        "TIER2_CORROBORATED": 0.9,
        "TIER3_ONLY": 0.5,
        "NONE": 0.2,
    }[corroboration.level.value]
    return min(1.0, credibility.credibility_weight * corroboration_multiplier)


def compute_severity(
    *,
    market_impact_score: float,
    corroboration: CorroborationResult,
) -> NewsSeverity:
    """Severity computation gated by the corroboration ceiling.

    Ceiling rule (hard, per spec section 14):
      - corroboration.can_support_high_or_critical is False -> ceiling MEDIUM
      - corroboration.can_support_high_or_critical is True  -> ceiling CRITICAL

    Within the ceiling, market_impact_score (0-1) maps to a severity
    level via fixed cutoffs (class F, unvalidated -- the exact cutoffs
    0.85/0.65/0.35 are starting hypotheses, not calibrated, consistent
    with every other class F threshold in this codebase).
    """
    if market_impact_score >= 0.85:
        raw_severity = NewsSeverity.CRITICAL
    elif market_impact_score >= 0.65:
        raw_severity = NewsSeverity.HIGH
    elif market_impact_score >= 0.35:
        raw_severity = NewsSeverity.MEDIUM
    else:
        raw_severity = NewsSeverity.LOW

    if corroboration.can_support_high_or_critical:
        return raw_severity

    # Ceiling applies: cap at MEDIUM regardless of how high the raw
    # market-impact score computed.
    ceiling_idx = _SEVERITY_ORDER.index(NewsSeverity.MEDIUM)
    raw_idx = _SEVERITY_ORDER.index(raw_severity)
    return _SEVERITY_ORDER[min(raw_idx, ceiling_idx)]


def assess_impact(
    *,
    item: ParsedNewsItem,
    credibility: SourceCredibility,
    corroboration: CorroborationResult,
    is_first_seen: bool,
) -> ImpactAssessment:
    novelty = compute_novelty_score(item, is_first_seen)
    market_impact = compute_market_impact_score(item.category, credibility, len(item.entities))
    confidence = compute_confidence(credibility, corroboration)
    severity = compute_severity(market_impact_score=market_impact, corroboration=corroboration)

    return ImpactAssessment(
        severity=severity, confidence=confidence,
        market_impact_score=market_impact, novelty_score=novelty,
    )
