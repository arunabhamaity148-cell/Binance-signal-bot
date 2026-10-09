from __future__ import annotations

import pytest

from app.core.models import Direction
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep


def _reclaim_factor(*, level: float, close: float, atr: float) -> float:
    breakdown = S1LiquiditySweep._confidence_breakdown(
        direction=Direction.LONG,
        base_confidence=0.62,
        taker_buy_ratio=0.60,
        sweep_distance=0.50,
        reclaim_distance=(level - close) / atr,
        reclaim_band_atr=0.15,
        penetration_min_atr=0.25,
        volume_ratio=1.5,
    )
    return breakdown["reclaim_quality_factor"]


def test_reclaim_depth_at_threshold_gets_half_credit():
    assert _reclaim_factor(level=100.0, close=99.9, atr=1.0) >= 0.5


def test_deeper_reclaim_gets_full_credit():
    assert _reclaim_factor(level=100.0, close=99.5, atr=1.0) == pytest.approx(1.0)


def test_close_above_swept_high_fails_closed():
    assert _reclaim_factor(level=100.0, close=100.1, atr=1.0) == 0.0
