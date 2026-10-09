"""News collectors: fetch RSS/JSON feeds for every source in
config/news_sources.yaml, with per-source timeout, retry with
exponential backoff + jitter, HTTP 429 handling with retry_after, TLS
verification enforced, and a source health checker.

Per spec section 14's mandatory component list: "source registry"
(config/news_sources.yaml itself), "source health checker",
"per-source timeout", "retry with exponential backoff + jitter",
"HTTP 429 handling with retry_after", "TLS verification enforced".

No real feed URLs are hardcoded here or anywhere in this module —
every URL comes from config/news_sources.yaml, which (per explicit
instruction) ships with provisional endpoint values pending operator
confirmation before deployment. This module works with whatever URL
the config supplies; it does not distinguish a confirmed endpoint from
an unconfirmed one.

TLS certificate validation stays on for every request this module
makes (httpx's default), with no code path anywhere in this module
that weakens or bypasses it — enforced both by this implementation and
by scripts/scan_forbidden_calls.py's static check.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum

import feedparser
import httpx

from app.core.errors import NewsSourceUnavailableError
from app.core.logging import get_logger
from app.news.parser import RawFeedItem

logger = get_logger(__name__)


class SourceFormat(str, Enum):
    RSS = "rss"
    JSON = "json"


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    tier: int
    format: SourceFormat
    timeout_s: float
    credibility_weight: float
    enabled: bool = True
    poll_interval_s: float = 60.0


@dataclass
class SourceHealthState:
    """Rolling health state for one source, updated after every fetch
    attempt. Used by the health checker (`assess_source_health`) and by
    the news engine to decide whether a category has enough healthy
    sources to support corroboration."""

    source_name: str
    last_success_ts_ms: int | None = None
    last_attempt_ts_ms: int | None = None
    consecutive_failures: int = 0
    total_attempts: int = 0
    total_successes: int = 0

    def record_success(self, now_ms: int) -> None:
        self.last_success_ts_ms = now_ms
        self.last_attempt_ts_ms = now_ms
        self.consecutive_failures = 0
        self.total_attempts += 1
        self.total_successes += 1

    def record_failure(self, now_ms: int) -> None:
        self.last_attempt_ts_ms = now_ms
        self.consecutive_failures += 1
        self.total_attempts += 1

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.total_successes / self.total_attempts


def build_source_configs(news_sources_cfg: dict) -> list[SourceConfig]:
    """Parse config/news_sources.yaml's tier_1/tier_2/tier_3 lists into
    SourceConfig objects."""
    out: list[SourceConfig] = []
    for tier_key, tier_num in (("tier_1", 1), ("tier_2", 2), ("tier_3", 3)):
        for entry in news_sources_cfg.get(tier_key, []):
            out.append(
                SourceConfig(
                    name=entry["name"],
                    url=entry["url"],
                    tier=tier_num,
                    format=SourceFormat(entry["format"]),
                    timeout_s=float(entry["timeout_s"]),
                    credibility_weight=float(entry["credibility_weight"]),
                    enabled=bool(entry.get("enabled", True)),
                    poll_interval_s=float(entry.get("poll_interval_s", 60.0)),
                )
            )
    return out


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int
    base_backoff_ms: int
    jitter_ms: int
    respect_retry_after_header: bool


def build_retry_config(news_sources_cfg: dict) -> RetryConfig:
    retry_cfg = news_sources_cfg["retry"]
    return RetryConfig(
        max_attempts=retry_cfg["max_attempts"],
        base_backoff_ms=retry_cfg["base_backoff_ms"],
        jitter_ms=retry_cfg["jitter_ms"],
        respect_retry_after_header=retry_cfg["respect_retry_after_header"],
    )


class NewsCollector:
    """Fetches one source's feed and parses it into RawFeedItems.
    TLS verification is always on (httpx's default; never overridden).
    """

    def __init__(self, retry_config: RetryConfig, client: httpx.AsyncClient | None = None) -> None:
        self._retry_config = retry_config
        self._client = client or httpx.AsyncClient(follow_redirects=True)
        self._health: dict[str, SourceHealthState] = {}

    async def close(self) -> None:
        await self._client.aclose()

    def health_for(self, source_name: str) -> SourceHealthState:
        if source_name not in self._health:
            self._health[source_name] = SourceHealthState(source_name=source_name)
        return self._health[source_name]

    def _backoff_delay_s(self, attempt: int) -> float:
        base = self._retry_config.base_backoff_ms * (2 ** (attempt - 1))
        jitter = random.uniform(0, self._retry_config.jitter_ms)
        return (base + jitter) / 1000.0

    async def fetch_source(self, source: SourceConfig) -> list[RawFeedItem]:
        """Fetch and parse one source. Raises NewsSourceUnavailableError
        on exhausted retries; never raises for an individual malformed
        entry within an otherwise-successful fetch (those are skipped
        and logged, per the fail-closed-per-item, not fail-closed-for-
        the-whole-fetch principle — one bad entry in an RSS feed
        shouldn't discard every other valid entry in it).
        """
        health = self.health_for(source.name)
        if not getattr(source, "enabled", True):
            health.disabled = True
            raise NewsSourceUnavailableError(f"{source.name}: source disabled by configuration")
        attempt = 0
        while True:
            attempt += 1
            now_ms = int(time.time() * 1000)
            try:
                resp = await self._client.get(source.url, timeout=source.timeout_s)
            except httpx.TimeoutException:
                health.record_failure(now_ms)
                if attempt >= self._retry_config.max_attempts:
                    raise NewsSourceUnavailableError(f"{source.name}: timed out after {attempt} attempts")
                await asyncio.sleep(self._backoff_delay_s(attempt))
                continue
            except httpx.HTTPError as exc:
                health.record_failure(now_ms)
                if attempt >= self._retry_config.max_attempts:
                    raise NewsSourceUnavailableError(f"{source.name}: fetch failed: {exc}") from exc
                await asyncio.sleep(self._backoff_delay_s(attempt))
                continue

            if resp.status_code == 429:
                health.record_failure(now_ms)
                if attempt >= self._retry_config.max_attempts:
                    raise NewsSourceUnavailableError(f"{source.name}: rate limited after {attempt} attempts")
                retry_after = resp.headers.get("Retry-After")
                if self._retry_config.respect_retry_after_header and retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = self._backoff_delay_s(attempt)
                else:
                    delay = self._backoff_delay_s(attempt)
                await asyncio.sleep(delay)
                continue

            if 500 <= resp.status_code < 600:
                health.record_failure(now_ms)
                if attempt >= self._retry_config.max_attempts:
                    raise NewsSourceUnavailableError(f"{source.name}: server error {resp.status_code}")
                await asyncio.sleep(self._backoff_delay_s(attempt))
                continue

            if resp.status_code != 200:
                health.record_failure(now_ms)
                raise NewsSourceUnavailableError(f"{source.name}: unexpected status {resp.status_code}")

            health.record_success(now_ms)
            fetched_ts_ms = now_ms
            return self._parse_response(source, resp.text, fetched_ts_ms)

    def _parse_response(self, source: SourceConfig, body: str, fetched_ts_ms: int) -> list[RawFeedItem]:
        if source.format == SourceFormat.RSS:
            return self._parse_rss(source, body, fetched_ts_ms)
        return self._parse_json(source, body, fetched_ts_ms)

    def _parse_rss(self, source: SourceConfig, body: str, fetched_ts_ms: int) -> list[RawFeedItem]:
        parsed = feedparser.parse(body)
        items: list[RawFeedItem] = []
        for entry in parsed.entries:
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not title or not link:
                continue  # skip malformed entry, keep the rest
            summary = getattr(entry, "summary", "") or ""
            published_ts_ms = self._entry_published_ms(entry, fetched_ts_ms)
            items.append(
                RawFeedItem(
                    source_name=source.name, tier=source.tier, title=title, summary=summary,
                    url=link, published_ts_ms=published_ts_ms, fetched_ts_ms=fetched_ts_ms,
                )
            )
        return items

    @staticmethod
    def _entry_published_ms(entry, fallback_ts_ms: int) -> int:
        parsed_time = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if parsed_time is None:
            return fallback_ts_ms  # no publish timestamp on the entry; fall back to fetch time
        return int(time.mktime(parsed_time) * 1000)

    def _parse_json(self, source: SourceConfig, body: str, fetched_ts_ms: int) -> list[RawFeedItem]:
        import json

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []  # malformed JSON body: no items, not a crash
        entries = data if isinstance(data, list) else data.get("items", data.get("data", []))
        items: list[RawFeedItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            url = entry.get("url") or entry.get("link")
            if not title or not url:
                continue
            summary = entry.get("summary", entry.get("description", "")) or ""
            published_ts_ms = int(entry.get("published_ts_ms", fetched_ts_ms))
            items.append(
                RawFeedItem(
                    source_name=source.name, tier=source.tier, title=title, summary=summary,
                    url=url, published_ts_ms=published_ts_ms, fetched_ts_ms=fetched_ts_ms,
                )
            )
        return items


def assess_source_health(health: SourceHealthState, *, min_success_rate: float = 0.5) -> bool:
    """A source is considered healthy if it has never been attempted
    (benefit of the doubt on first use) or its rolling success rate is
    at or above `min_success_rate`. `min_success_rate` is an
    engineering default (not a class E/F config value — NEWS_REGISTRY.md
    specifies a health checker exists but not an exact threshold for
    it), defined once here rather than per-caller."""
    if health.total_attempts == 0:
        return True
    return health.success_rate >= min_success_rate
