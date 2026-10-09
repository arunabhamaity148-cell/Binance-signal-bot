from __future__ import annotations

from app.config import load_all
from app.core.models import TakerFlowState
from app.core.math import OHLC
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep
from tests.strategies.test_s1_liquidity_sweep import _base_snapshot, _empty_news, _flat_bars


def test_s1_partial_reclaim_quality_still_creates_candidate():
    base_price = 100.0
    bars = _flat_bars(65, base_price)
    swing_bars = [
        OHLC(
            open=base_price, high=base_price + 5, low=base_price - 5,
            close=base_price, volume=100,
            close_time_ms=bars[-1].close_time_ms + 300_000 * (i + 1),
        )
        for i in range(3)
    ]
    prev_bar = OHLC(
        open=base_price, high=base_price + 5, low=base_price - 20,
        close=base_price - 2, volume=100,
        close_time_ms=swing_bars[-1].close_time_ms + 300_000,
    )
    l_high = base_price + 5
    atr_approx = 10.0
    trigger_bar = OHLC(
        open=base_price, high=l_high + 0.5 * atr_approx, low=base_price - 3,
        close=l_high - 0.15 * atr_approx, volume=300,
        close_time_ms=prev_bar.close_time_ms + 300_000,
    )
    all_bars = bars + swing_bars + [prev_bar, trigger_bar]
    as_of = trigger_bar.close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[10, 10, 20],
        total_volume_last_bars=[10, 10, 20], event_ts_ms=as_of,
        received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(all_bars, taker_flow, as_of)

    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(as_of), load_all().strategy)

    assert len(result) >= 1
    candidate = result[0]
    assert candidate.strategy_source == "S1"
    breakdown = candidate.meta["confidence_breakdown"]
    assert 0.0 < breakdown["reclaim_quality_factor"] < 1.0
    assert breakdown["taker_flow_factor"] == 1.0
    assert breakdown["sweep_distance_factor"] > 0.8
    assert breakdown["volume_factor"] == 1.0
    assert breakdown["final"] > 0.55
