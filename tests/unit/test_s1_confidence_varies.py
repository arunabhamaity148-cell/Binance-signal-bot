from __future__ import annotations

from app.core.models import Direction
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep


def _confidence(*, taker: float, sweep: float, reclaim: float, volume: float) -> float:
    return S1LiquiditySweep._confidence_breakdown(
        direction=Direction.SHORT, base_confidence=0.62,
        taker_buy_ratio=taker, sweep_distance=sweep, reclaim_distance=reclaim,
        reclaim_band_atr=0.15, penetration_min_atr=0.25, volume_ratio=volume,
    )["final"]


def test_s1_confidence_varies_with_setup_quality_and_strong_can_clear_grade_b():
    weak = _confidence(taker=0.449, sweep=0.251, reclaim=0.149, volume=0.5)
    medium = _confidence(taker=0.30, sweep=0.60, reclaim=0.07, volume=1.0)
    strong = _confidence(taker=0.05, sweep=1.20, reclaim=0.01, volume=2.0)

    assert len({round(weak, 10), round(medium, 10), round(strong, 10)}) == 3
    assert weak < 0.55
    assert strong > 0.55
