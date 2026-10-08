from __future__ import annotations

import logging

from app.core.models import FeedHealth
from app.data.binance.health import assess_feed_health


CFG = {"feed_gap_threshold_ms": 10_000, "max_reconnects_per_window": 5}


def _health(last_message_ts_ms: int) -> FeedHealth:
    return FeedHealth(
        symbol="NEARUSDT", stream="nearusdt@aggTrade",
        last_message_received_ts_ms=last_message_ts_ms,
        reconnect_count_window=0, is_connected=True,
    )


def test_healthy_feed_100ms_old_passes_and_reports_true_gap(caplog):
    caplog.set_level(logging.INFO)
    assessment = assess_feed_health(_health(1_791_500_000_000), as_of_ts_ms=1_791_500_000_100, **CFG)
    assert assessment.is_healthy
    assert assessment.gap_ms == 100
    assert "G2_check" in caplog.text
    assert "gap_ms=100" in caplog.text


def test_stale_feed_15_seconds_old_blocks_with_true_gap():
    assessment = assess_feed_health(_health(1_791_500_000_000), as_of_ts_ms=1_791_500_015_000, **CFG)
    assert not assessment.is_healthy
    assert assessment.gap_ms == 15_000
    assert assessment.reason == "gap 15000ms exceeds threshold 10000ms"


def test_feed_health_timestamps_are_epoch_milliseconds():
    now_ms = 1_791_500_000_100
    last_message_ms = 1_791_500_000_000
    assert len(str(now_ms)) == 13
    assert len(str(last_message_ms)) == 13
    assessment = assess_feed_health(_health(last_message_ms), as_of_ts_ms=now_ms, **CFG)
    assert assessment.gap_ms == 100


def test_future_last_message_clamps_gap_to_zero():
    assessment = assess_feed_health(_health(1_791_500_001_000), as_of_ts_ms=1_791_500_000_100, **CFG)
    assert assessment.is_healthy
    assert assessment.gap_ms == 0
