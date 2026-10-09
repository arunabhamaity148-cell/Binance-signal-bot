from __future__ import annotations

import logging
import json
from dataclasses import replace

import pytest

from app.config import load_all
from app.core.errors import NewsSourceUnavailableError
from app.news.collectors import NewsCollector, RetryConfig
from app.news.engine import NewsEngine, run_collection_cycle
from app.strategies.s3_funding_crowding import S3FundingCrowding
from tests.strategies.test_s3_funding_crowding import _empty_news, build_realistic_s3_short_reversal_snapshot


class _FailingCollector:
    def __init__(self):
        self._health = type("Health", (), {"total_attempts": 4, "consecutive_failures": 4})()
        self._health.success_rate = 0.0

    async def fetch_source(self, source):
        raise NewsSourceUnavailableError("test_source: timed out after 4 attempts")

    def health_for(self, source_name):
        return self._health


def test_news_failure_diagnostic_contains_underlying_cause(caplog):
    cfg = load_all().news_sources
    engine = NewsEngine(cfg)
    source = type("Source", (), {"name": "test_source"})()
    with caplog.at_level(logging.INFO):
        import asyncio
        asyncio.run(run_collection_cycle(_FailingCollector(), engine, [source], receipt_ts_ms=1))
    record = next(r for r in caplog.records if r.getMessage().startswith("diag_news |"))
    payload = json.loads(record.getMessage().split(" | ", 1)[1])
    assert payload["reason"] == "NewsSourceUnavailableError"
    assert payload["values"]["error"] == "test_source: timed out after 4 attempts"
    assert payload["values"]["attempts"] == 4


def test_s3_freshness_diagnostic_reports_ages_and_thresholds(caplog):
    snapshot = build_realistic_s3_short_reversal_snapshot()
    with caplog.at_level(logging.INFO, logger="app.strategies.s3_funding_crowding"):
        S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), load_all().strategy)
    record = next(r for r in caplog.records if r.getMessage().startswith("s3_data_check"))
    payload = getattr(record, "context", None) or {}
    if not payload:
        # The application formatter renders the context as JSON in the message.
        payload = json.loads(record.getMessage().split(" | ", 1)[1])
    assert {"funding_age_ms", "oi_age_ms", "ls_ratio_age_ms", "required_max", "ok"} <= set(payload)
    assert payload["ok"] is True
