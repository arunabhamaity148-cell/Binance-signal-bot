from __future__ import annotations

from app.core.time_utils import (
    hours_to_ms,
    is_stale,
    iso8601_utc,
    minutes_to_ms,
    ms_to_minutes,
    now_ms,
    utc_date_str,
)


def test_now_ms_is_positive_int():
    assert isinstance(now_ms(), int)
    assert now_ms() > 0


def test_is_stale_fresh_data_not_stale():
    event = 1_000_000
    received = 1_000_100
    as_of = 1_000_200
    assert not is_stale(
        event_ts_ms=event, received_ts_ms=received, as_of_ts_ms=as_of, staleness_budget_ms=5000
    )


def test_is_stale_high_processing_lag():
    event = 1_000_000
    received = 1_010_000  # 10s lag
    as_of = 1_010_100
    assert is_stale(
        event_ts_ms=event, received_ts_ms=received, as_of_ts_ms=as_of, staleness_budget_ms=5000
    )


def test_is_stale_aged_since_ingestion():
    event = 1_000_000
    received = 1_000_100
    as_of = 1_010_000  # aged since receipt
    assert is_stale(
        event_ts_ms=event, received_ts_ms=received, as_of_ts_ms=as_of, staleness_budget_ms=5000
    )


def test_utc_date_str_format():
    # 2026-09-27T00:00:00Z
    ts_ms = 1790812800000
    result = utc_date_str(ts_ms)
    assert len(result) == 8
    assert result.isdigit()


def test_iso8601_utc_format():
    ts_ms = 1790812800123
    result = iso8601_utc(ts_ms)
    assert result.endswith("Z")
    assert "T" in result
    assert ".123Z" in result


def test_minutes_to_ms():
    assert minutes_to_ms(1) == 60_000
    assert minutes_to_ms(0.5) == 30_000


def test_hours_to_ms():
    assert hours_to_ms(1) == 3_600_000


def test_ms_to_minutes():
    assert ms_to_minutes(60_000) == 1.0
