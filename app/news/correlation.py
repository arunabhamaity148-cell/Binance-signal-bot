"""Corroboration logic: the global-block rule from spec section 14 and
NEWS_REGISTRY.md, implemented exactly:

    A market-wide HIGH/CRITICAL block requires:
      EITHER: one verified Tier-1 source
      OR:     two independent Tier-2 sources (distinct canonical_url
              AND distinct domain)

    A single Tier-3 source can NEVER produce HIGH or CRITICAL severity,
    regardless of corroboration count among only Tier-3 sources. This
    is a hard ceiling enforced here, not merely a scoring outcome that
    happens to fall short.

This module determines whether a GROUP of related ParsedNewsItems
(items about the same underlying event — grouped by the caller, e.g.
by matching category + overlapping entities + close publish times; see
engine.py) has enough corroboration to support HIGH/CRITICAL severity.
It does not itself decide severity (that is impact.py's job); it
answers the narrower question "is this group corroborated enough for
the given tier composition to justify HIGH/CRITICAL", which impact.py
then combines with other factors.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.news.parser import ParsedNewsItem


class CorroborationLevel(str, Enum):
    NONE = "NONE"
    TIER3_ONLY = "TIER3_ONLY"  # ceiling: MEDIUM max, never HIGH/CRITICAL
    TIER2_CORROBORATED = "TIER2_CORROBORATED"  # two independent Tier-2 sources
    TIER1_VERIFIED = "TIER1_VERIFIED"  # one Tier-1 source


@dataclass(frozen=True)
class CorroborationResult:
    level: CorroborationLevel
    can_support_high_or_critical: bool
    contributing_sources: tuple[str, ...]
    reason: str


def _are_independent(a: ParsedNewsItem, b: ParsedNewsItem) -> bool:
    """Two Tier-2 items are 'independent' sources per spec if they have
    distinct canonical URLs AND distinct domains. Same domain (even
    with a different URL, e.g. two different articles on the same
    outlet) does NOT count as independent corroboration — it's still
    one outlet's editorial judgment."""
    return a.canonical_url != b.canonical_url and a.domain != b.domain


def assess_corroboration(group: list[ParsedNewsItem]) -> CorroborationResult:
    """Assess the corroboration level for a group of ParsedNewsItems
    believed to describe the same underlying event.

    Precedence: a verified Tier-1 source alone is sufficient and is
    checked first (spec: "one Tier-1 source, verified" — verification
    here means the item exists as a successfully parsed ParsedNewsItem
    at all, since parsing itself already required successful fetch +
    entity/category extraction; there is no additional "verification"
    step beyond that in this implementation, matching NEWS_REGISTRY.md
    which does not specify further Tier-1 verification steps beyond
    successful ingestion).
    """
    if not group:
        return CorroborationResult(
            level=CorroborationLevel.NONE, can_support_high_or_critical=False,
            contributing_sources=(), reason="empty group",
        )

    tier1_items = [i for i in group if i.tier == 1]
    if tier1_items:
        return CorroborationResult(
            level=CorroborationLevel.TIER1_VERIFIED,
            can_support_high_or_critical=True,
            contributing_sources=tuple(i.source_name for i in tier1_items),
            reason=f"{len(tier1_items)} Tier-1 source(s) present; Tier-1 alone is sufficient",
        )

    tier2_items = [i for i in group if i.tier == 2]
    for idx_a in range(len(tier2_items)):
        for idx_b in range(idx_a + 1, len(tier2_items)):
            a, b = tier2_items[idx_a], tier2_items[idx_b]
            if _are_independent(a, b):
                return CorroborationResult(
                    level=CorroborationLevel.TIER2_CORROBORATED,
                    can_support_high_or_critical=True,
                    contributing_sources=(a.source_name, b.source_name),
                    reason=(
                        f"two independent Tier-2 sources: {a.source_name} ({a.domain}) "
                        f"and {b.source_name} ({b.domain})"
                    ),
                )

    tier3_items = [i for i in group if i.tier == 3]
    if tier3_items or tier2_items:
        # Tier-3 items present (alone or alongside a single,
        # non-independently-corroborated Tier-2 item) can never clear
        # the HIGH/CRITICAL ceiling — hard rule, not a scoring
        # shortfall that a config change could quietly relax.
        return CorroborationResult(
            level=CorroborationLevel.TIER3_ONLY,
            can_support_high_or_critical=False,
            contributing_sources=tuple(i.source_name for i in group),
            reason=(
                "insufficient corroboration for HIGH/CRITICAL: no Tier-1 source and "
                "fewer than two independent Tier-2 sources; Tier-3 sources can never "
                "independently raise severity above MEDIUM regardless of count"
            ),
        )

    return CorroborationResult(
        level=CorroborationLevel.NONE, can_support_high_or_critical=False,
        contributing_sources=(), reason="no recognized-tier sources in group",
    )


def group_related_items(
    items: list[ParsedNewsItem], *, time_window_ms: int = 3_600_000
) -> list[list[ParsedNewsItem]]:
    """Group items believed to describe the same underlying event:
    same category, overlapping entities (or both market-wide), and
    published within `time_window_ms` of each other (default 1 hour —
    an engineering choice for "close enough in time to plausibly be
    the same story", not a config-driven market threshold, so it is a
    function default rather than a class E/F config value).

    This is intentionally simple (no NLP/similarity scoring) —
    NEWS_REGISTRY.md does not specify a more sophisticated grouping
    algorithm, and a simple, auditable rule is preferable to an opaque
    one for a risk-control subsystem (spec section 14: "News must
    behave as a RISK-CONTROL subsystem, not random sentiment").
    """
    groups: list[list[ParsedNewsItem]] = []
    used: set[int] = set()

    for i, item in enumerate(items):
        if i in used:
            continue
        group = [item]
        used.add(i)
        for j in range(i + 1, len(items)):
            if j in used:
                continue
            other = items[j]
            same_category = other.category == item.category
            entities_overlap = bool(set(item.entities) & set(other.entities))
            within_window = abs(other.published_ts_ms - item.published_ts_ms) <= time_window_ms
            if same_category and entities_overlap and within_window:
                group.append(other)
                used.add(j)
        groups.append(group)

    return groups
