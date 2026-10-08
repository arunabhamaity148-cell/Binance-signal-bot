"""Credibility scoring by tier, per spec section 14's mandatory
component "credibility scoring by tier".

Per-source credibility weights live entirely in
config/news_sources.yaml (each source entry's `credibility_weight`) —
this module never hardcodes a weight; it looks the configured value up
by source name so there is exactly one place (the config file) where
these numbers are set, consistent with every other threshold in this
codebase. Intra-tier adjustments (e.g. Cointelegraph weighted below
CoinDesk/The Block within Tier 2) are already expressed as different
per-source `credibility_weight` values in the config — this module
does not re-implement that distinction, it reads it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceCredibility:
    source_name: str
    tier: int
    credibility_weight: float


def build_credibility_index(news_sources_cfg: dict) -> dict[str, SourceCredibility]:
    """Build a source_name -> SourceCredibility lookup from the parsed
    config/news_sources.yaml structure. Called once at engine startup
    (or whenever config is reloaded); the resulting index is passed to
    `lookup_credibility` by callers that need per-item scoring.
    """
    index: dict[str, SourceCredibility] = {}
    for tier_key, tier_num in (("tier_1", 1), ("tier_2", 2), ("tier_3", 3)):
        for entry in news_sources_cfg.get(tier_key, []):
            name = entry["name"]
            index[name] = SourceCredibility(
                source_name=name, tier=tier_num, credibility_weight=entry["credibility_weight"]
            )
    return index


def lookup_credibility(source_name: str, index: dict[str, SourceCredibility]) -> SourceCredibility:
    """Raises KeyError for an unknown source name — an item from a
    source not present in config/news_sources.yaml is a configuration
    inconsistency, not a recoverable runtime condition, so this fails
    loudly rather than guessing a default weight."""
    if source_name not in index:
        raise KeyError(f"unknown news source (not in config/news_sources.yaml): {source_name!r}")
    return index[source_name]
