"""News engine: orchestrates collectors -> parser -> deduper ->
credibility -> correlation -> impact -> decay into a NewsState.

Per spec section 14: "If news health is insufficient, fail closed for
the affected category only — do not freeze the whole system." This
module is where that rule is implemented: `build_news_state` computes
`unhealthy_categories` (categories whose contributing sources are
collectively unhealthy enough that corroboration can't be trusted) and
excludes events in those categories from `active_events`, while every
OTHER category's events are included normally. The system never
produces an all-or-nothing NewsState.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.models import NewsCategory, NewsEvent, NewsState
from app.news.collectors import (
    NewsCollector,
    SourceConfig,
    assess_source_health,
    build_retry_config,
    build_source_configs,
)
from app.news.correlation import assess_corroboration, group_related_items
from app.news.credibility import build_credibility_index, lookup_credibility
from app.news.decay import is_active
from app.news.deduper import DedupStore, dedupe_batch
from app.news.impact import assess_impact
from app.news.parser import ParsedNewsItem, parse_raw_item
from app.monitoring.diagnostics import news as diagnostic_news
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class NewsEngineConfig:
    sources: list[SourceConfig]
    category_half_life_hours: dict
    min_source_success_rate: float = 0.5


def build_news_engine_config(news_sources_cfg: dict) -> NewsEngineConfig:
    return NewsEngineConfig(
        sources=build_source_configs(news_sources_cfg),
        category_half_life_hours=news_sources_cfg["category_half_life_hours"],
    )


class NewsEngine:
    """Owns the dedup store and credibility index across polling
    cycles, and produces a NewsState on demand from the latest known
    parsed items. Fetching (via NewsCollector) is a separate concern
    the caller (main.py / bot.py, not built in this batch) drives on
    its own schedule; this engine's `ingest_batch` and
    `build_news_state` are the pure-ish orchestration steps that don't
    themselves need to know about asyncio scheduling.
    """

    def __init__(self, news_sources_cfg: dict) -> None:
        self._credibility_index = build_credibility_index(news_sources_cfg)
        self._dedup_store = DedupStore()
        self._known_items: list[ParsedNewsItem] = []
        self._category_half_life_hours = news_sources_cfg["category_half_life_hours"]
        self._source_health: dict[str, bool] = {}

    def ingest_batch(self, items: list[ParsedNewsItem]) -> list[ParsedNewsItem]:
        """Deduplicate and accumulate a batch of freshly-parsed items.
        Returns only the genuinely-new items from this batch (for
        logging/auditing purposes); all accepted items are retained
        internally for `build_news_state` to consider."""
        new_items = dedupe_batch(items, self._dedup_store)
        self._known_items.extend(new_items)
        return new_items

    def record_source_health(self, source_name: str, is_healthy: bool) -> None:
        self._source_health[source_name] = is_healthy

    def _category_is_healthy(self, category: NewsCategory) -> bool:
        """A category is healthy if at least one source that has
        reported items in that category is currently healthy, OR if no
        health information has been recorded at all yet (benefit of
        the doubt before the first health assessment runs — matches
        assess_source_health's own first-use default)."""
        relevant_sources = {item.source_name for item in self._known_items if item.category == category}
        if not relevant_sources:
            return True  # no sources to judge; nothing to fail closed on for this category yet
        reported = {s: self._source_health[s] for s in relevant_sources if s in self._source_health}
        if not reported:
            return True
        return any(reported.values())

    def build_news_state(self, as_of_ts_ms: int) -> NewsState:
        """Build the current NewsState: group known items into
        same-event clusters, assess corroboration and impact for each
        group, filter to only still-active (non-decayed) events, and
        exclude any category whose sources are collectively unhealthy.
        """
        groups = group_related_items(self._known_items)
        events: list[NewsEvent] = []
        unhealthy_categories: set[NewsCategory] = set()

        for group in groups:
            if not group:
                continue
            category = group[0].category
            if not self._category_is_healthy(category):
                unhealthy_categories.add(category)
                continue  # fail closed for THIS category only

            corroboration = assess_corroboration(group)
            representative = group[0]
            credibility = lookup_credibility(representative.source_name, self._credibility_index)
            is_first_seen = True  # items reaching here already passed dedup -> first-seen by definition
            impact = assess_impact(
                item=representative, credibility=credibility, corroboration=corroboration,
                is_first_seen=is_first_seen,
            )

            if not is_active(
                published_ts_ms=representative.published_ts_ms, as_of_ts_ms=as_of_ts_ms,
                category=category, category_half_life_hours=self._category_half_life_hours,
            ):
                continue  # decayed out, not currently active

            all_entities: set[str] = set()
            for item in group:
                all_entities.update(item.entities)

            events.append(
                NewsEvent(
                    event_id=uuid.uuid4().hex,
                    source_name=representative.source_name,
                    tier=min(i.tier for i in group),  # best (lowest-numbered) tier represented
                    canonical_url=representative.canonical_url,
                    domain=representative.domain,
                    published_ts_ms=representative.published_ts_ms,
                    fetched_ts_ms=representative.fetched_ts_ms,
                    receipt_ts_ms=representative.receipt_ts_ms,
                    category=category,
                    direction=representative.direction,
                    entities=tuple(sorted(all_entities)),
                    credibility_weight=credibility.credibility_weight,
                    confidence=impact.confidence,
                    severity=impact.severity,
                    content_hash=representative.content_hash,
                )
            )

        return NewsState(
            as_of_ts_ms=as_of_ts_ms,
            active_events=tuple(events),
            unhealthy_categories=frozenset(unhealthy_categories),
        )


async def run_collection_cycle(
    collector: NewsCollector, engine: NewsEngine, sources: list[SourceConfig], *, receipt_ts_ms: int
) -> None:
    """One full collection cycle: fetch every source, parse, ingest.
    A single source's failure (NewsSourceUnavailableError) degrades
    only that source — it is caught here and recorded as unhealthy,
    never allowed to abort the cycle for other sources."""
    from app.core.errors import NewsSourceUnavailableError

    for source in sources:
        try:
            raw_items = await collector.fetch_source(source)
            parsed = [parse_raw_item(item, receipt_ts_ms=receipt_ts_ms) for item in raw_items]
            new_items = engine.ingest_batch(parsed)
            diagnostic_news("NEWS", source.name, "success", "source_cycle", {"parsed": len(parsed), "new_items": len(new_items)})
            health = collector.health_for(source.name)
            engine.record_source_health(source.name, assess_source_health(health))
        except NewsSourceUnavailableError as exc:
            health = collector.health_for(source.name)
            details = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "attempts": health.total_attempts,
                "consecutive_failures": health.consecutive_failures,
            }
            logger.warning("news_source_failed", extra={"context": {"source": source.name, **details}})
            diagnostic_news("NEWS", source.name, "failed", type(exc).__name__, details)
            engine.record_source_health(source.name, assess_source_health(health))
            continue
