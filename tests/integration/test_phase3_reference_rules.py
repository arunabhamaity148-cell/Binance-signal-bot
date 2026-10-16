from __future__ import annotations

import pytest

from app.backtest.costs import CostScenario, compute_cost_breakdown
from app.backtest.engine import _compute_realized_r, _search_forward_for_fill
from app.backtest.fills import PositionState, force_close_position, process_bar_for_open_position
from app.backtest.metrics import TradeResult, compute_metrics, split_reference_and_operator_results
from app.core.math import OHLC
from app.core.models import Direction


def _bar(open_, high, low, close, ts):
    return OHLC(open=open_, high=high, low=low, close=close, volume=10.0, close_time_ms=ts)


def test_entry_search_never_fills_same_bar_and_stops_at_entry_expiry():
    class Candidate:
        entry_low = 99.0
        entry_high = 101.0
        direction = Direction.LONG

    bars = [_bar(100, 105, 95, 100, 100), _bar(100, 102, 98, 100, 200), _bar(100, 102, 98, 100, 300)]
    # The first eligible bar is before expiry; moving expiry to 200 excludes
    # the second bar because expiry is an exclusive boundary.
    assert _search_forward_for_fill(
        bars, start_idx=1, candidate=Candidate(), max_search_bars=10,
        expiry_ts_ms=200, signal_id="fixture",
    ) is None


def test_cost_scenario_is_not_silently_ignored():
    base = compute_cost_breakdown(
        entry_price=100, stop_price=95, fee_maker_bps=1, fee_taker_bps=4,
        notional_usd=1000, depth_usd=100_000,
    )
    stressed = compute_cost_breakdown(
        entry_price=100, stop_price=95, fee_maker_bps=1, fee_taker_bps=4,
        notional_usd=1000, depth_usd=100_000,
        spread_bps=8, basis_bps=5, scenario_slippage_bps=7,
    )
    assert stressed.round_trip_at_tp > base.round_trip_at_tp
    assert stressed.round_trip_at_sl > base.round_trip_at_sl
    assert CostScenario(name="stress", spread_bps=8, basis_bps=5).name == "stress"


def test_short_side_realized_r_and_time_stop_are_directionally_correct():
    pos = PositionState(
        direction=Direction.SHORT, entry_price=100, stop_loss=105,
        tp_levels=(95, 90, 85, 80), partial_exit_fractions=(0.4, 0.3, 0.2, 0.1),
    )
    pos = process_bar_for_open_position(pos, _bar(100, 101, 94, 95, 200))
    force_close_position(pos, price=98, bar_close_time_ms=300)

    class Candidate:
        stop_loss = 105.0
        direction = Direction.SHORT

    # 40% at +1R, remaining 60% at +0.4R = +0.64R.
    assert _compute_realized_r(pos, Candidate()) == pytest.approx(0.64)
    assert pos.exits[-1].exit_kind == "TIME_STOP"


def test_metrics_keep_reference_and_operator_outcomes_separate():
    reference = TradeResult("BTCUSDT", "S1", "LONG", 1.0, True, 1.2, -0.2, "default", 1, 2)
    operator = TradeResult("BTCUSDT", "S1", "LONG", -0.5, True, 0.3, -1.0, "default", 3, 4,
                           reference_path="DELTA_OPERATOR", operator_reported=True)
    ref, op = split_reference_and_operator_results([reference, operator])
    assert ref == [reference]
    assert op == [operator]
    assert compute_metrics(ref).net_r == pytest.approx(1.0)
    assert compute_metrics(op).net_r == pytest.approx(-0.5)
