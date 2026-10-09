from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.config import load_all
from app.strategies.s3_funding_crowding import S3FundingCrowding
from tests.strategies.test_s3_funding_crowding import (
    _empty_news,
    build_realistic_s3_short_reversal_snapshot,
)


@pytest.mark.parametrize(
    ("funding_age_ms", "expected_ok"),
    [
        (7 * 60 * 60 * 1000 + 59 * 60 * 1000, True),
        (9 * 60 * 60 * 1000 + 40 * 60 * 1000, False),
        (60 * 60 * 1000, True),
    ],
)
def test_s3_funding_freshness_threshold(caplog, funding_age_ms, expected_ok):
    snapshot = build_realistic_s3_short_reversal_snapshot()
    latest = snapshot.derivatives.funding_rate_history[-1]
    latest = replace(
        latest,
        event_ts_ms=snapshot.as_of_ts_ms - funding_age_ms,
        received_ts_ms=snapshot.as_of_ts_ms - funding_age_ms,
    )
    history = [*snapshot.derivatives.funding_rate_history[:-1], latest]
    snapshot = replace(snapshot, derivatives=replace(snapshot.derivatives, funding_rate_history=history))

    with caplog.at_level("INFO", logger="app.strategies.s3_funding_crowding"):
        S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), load_all().strategy)

    record = next(r for r in caplog.records if r.getMessage().startswith("s3_data_check"))
    payload = getattr(record, "context", None) or json.loads(record.getMessage().split(" | ", 1)[1])
    assert payload["ok"] is expected_ok
    assert payload["required_max"]["funding_ms"] == 34_200_000
