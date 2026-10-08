"""Feed health evaluation shared by guard G2 and monitoring/health.py.

This module contains the pure logic for deciding whether a feed is
healthy given its FeedHealth state and a staleness budget; the actual
FeedHealth state is produced by the WebSocket client
(data/binance/websocket.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import FeedHealth


@dataclass(frozen=True)
class FeedHealthAssessment:
    stream: str
    is_healthy: bool
    reason: str | None
    gap_ms: int | None
    reconnect_count_window: int


def assess_feed_health(
    health: FeedHealth,
    *,
    as_of_ts_ms: int,
    feed_gap_threshold_ms: int,
    max_reconnects_per_window: int,
) -> FeedHealthAssessment:
    """Pure evaluation of one feed's health. Used directly by guard G2
    (app/risk/veto.py) and by the monitoring health endpoint."""
    if not health.is_connected:
        return FeedHealthAssessment(
            stream=health.stream,
            is_healthy=False,
            reason="disconnected",
            gap_ms=None,
            reconnect_count_window=health.reconnect_count_window,
        )

    gap_ms = as_of_ts_ms - health.last_message_received_ts_ms
    if gap_ms > feed_gap_threshold_ms:
        return FeedHealthAssessment(
            stream=health.stream,
            is_healthy=False,
            reason=f"gap {gap_ms}ms exceeds threshold {feed_gap_threshold_ms}ms",
            gap_ms=gap_ms,
            reconnect_count_window=health.reconnect_count_window,
        )

    if health.reconnect_count_window > max_reconnects_per_window:
        return FeedHealthAssessment(
            stream=health.stream,
            is_healthy=False,
            reason=(
                f"reconnect storm: {health.reconnect_count_window} reconnects "
                f"exceeds {max_reconnects_per_window} in window"
            ),
            gap_ms=gap_ms,
            reconnect_count_window=health.reconnect_count_window,
        )

    return FeedHealthAssessment(
        stream=health.stream,
        is_healthy=True,
        reason=None,
        gap_ms=gap_ms,
        reconnect_count_window=health.reconnect_count_window,
    )


def all_healthy(assessments: list[FeedHealthAssessment]) -> bool:
    return all(a.is_healthy for a in assessments)


def unhealthy_reasons(assessments: list[FeedHealthAssessment]) -> list[str]:
    return [f"{a.stream}: {a.reason}" for a in assessments if not a.is_healthy]
