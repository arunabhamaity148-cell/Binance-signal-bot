from __future__ import annotations

import pytest

from app.core.models import Direction
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep


def _breakdown(*, taker: float, sweep: float, reclaim_distance: float, volume: float) -> dict[str, float]:
    return S1LiquiditySweep._confidence_breakdown(
        direction=Direction.LONG,
        base_confidence=0.62,
        taker_buy_ratio=taker,
        sweep_distance=sweep,
        reclaim_distance=reclaim_distance,
        reclaim_band_atr=0.15,
        penetration_min_atr=0.25,
        volume_ratio=volume,
    )


def test_threshold_setup_gives_proportional_credit_and_clears_grade_b():
    breakdown = _breakdown(
        taker=0.55, sweep=0.25, reclaim_distance=0.10, volume=1.0,
    )

    assert breakdown["taker_flow_factor"] == pytest.approx(0.5)
    assert breakdown["reclaim_quality_factor"] == pytest.approx(1 / 3)
    assert breakdown["sweep_distance_factor"] == pytest.approx(0.5)
    assert breakdown["volume_factor"] == pytest.approx(0.5)
    assert breakdown["final"] >= 0.55


def test_two_times_threshold_setup_gives_each_factor_full_credit():
    breakdown = _breakdown(
        taker=0.60, sweep=0.50, reclaim_distance=0.30, volume=1.5,
    )

    for name in (
        "taker_flow_factor",
        "reclaim_quality_factor",
        "sweep_distance_factor",
        "volume_factor",
    ):
        assert breakdown[name] == pytest.approx(1.0)
    assert breakdown["final"] >= 0.65


def test_below_threshold_setup_keeps_partial_positive_credit():
    breakdown = _breakdown(
        taker=0.54, sweep=0.25, reclaim_distance=0.10, volume=1.0,
    )

    assert breakdown["taker_flow_factor"] == pytest.approx(0.4)
    assert breakdown["final"] > 0.55
