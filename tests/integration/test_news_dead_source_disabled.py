from __future__ import annotations

import pytest

from app.core.errors import NewsSourceUnavailableError
from app.news.collectors import (
    NewsCollector,
    RetryConfig,
    SourceConfig,
    SourceFormat,
    SourceHealthState,
    assess_source_health,
)


_FAST = RetryConfig(max_attempts=1, base_backoff_ms=1, jitter_ms=1, respect_retry_after_header=True)


def test_five_consecutive_failures_disable_source(caplog):
    health = SourceHealthState(source_name="dead")
    with caplog.at_level("INFO", logger="app.news.collectors"):
        for now_ms in range(5):
            health.record_failure(now_ms)

    assert health.disabled is True
    assert health.consecutive_failures == 5
    assert assess_source_health(health) is False
    assert any("news_source_disabled" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_disabled_source_is_not_fetched():
    collector = NewsCollector(_FAST)
    source = SourceConfig("disabled", "https://example.com/feed", 2, SourceFormat.RSS, 5.0, 0.7, enabled=False)
    try:
        with pytest.raises(NewsSourceUnavailableError, match="source disabled by configuration"):
            await collector.fetch_source(source)
        health = collector.health_for("disabled")
        assert health.disabled is True
        assert health.total_attempts == 0
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_source_disabled_after_five_failures_is_not_fetched():
    collector = NewsCollector(_FAST)
    source = SourceConfig("dead", "https://example.com/feed", 2, SourceFormat.RSS, 5.0, 0.7)
    health = collector.health_for("dead")
    for now_ms in range(5):
        health.record_failure(now_ms)
    try:
        with pytest.raises(NewsSourceUnavailableError, match="disabled after 5 consecutive failures"):
            await collector.fetch_source(source)
        assert health.total_attempts == 5
    finally:
        await collector.close()
