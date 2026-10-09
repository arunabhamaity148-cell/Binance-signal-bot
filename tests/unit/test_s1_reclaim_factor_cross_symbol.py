from __future__ import annotations

import pytest

from app.core.models import Direction
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep


def _factor(*, level: float, close: float, atr: float) -> float:
    return S1LiquiditySweep._confidence_breakdown(
        direction=Direction.LONG,
        base_confidence=0.62,
        taker_buy_ratio=0.60,
        sweep_distance=0.50,
        reclaim_distance=(level - close) / atr,
        reclaim_band_atr=0.15,
        penetration_min_atr=0.25,
        volume_ratio=1.5,
    )["reclaim_quality_factor"]


def test_same_relative_reclaim_has_same_factor_across_btc_and_sol_scales():
    btc_factor = _factor(level=68_500.0, close=68_470.0, atr=50.0)
    sol_factor = _factor(level=150.0, close=149.4, atr=1.0)

    assert btc_factor == pytest.approx(sol_factor)
    assert btc_factor == pytest.approx(1.0)
