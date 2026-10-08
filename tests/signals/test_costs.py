"""Tests for the corrected cost model (app/backtest/costs.py).

These pin the design decision: entry and TP legs are MAKER, the stop
leg is TAKER, and the two round-trip figures answer different
questions (R:R at TP2 vs. cash risk at the stop).
"""
from __future__ import annotations

import pytest

from app.backtest.costs import (
    CostBreakdown,
    cash_risk_per_unit,
    compute_cost_breakdown,
    compute_rr_at_tp,
    estimate_slippage_bps,
)
from app.core.models import Direction, MarketSnapshot, OrderBookState
from app.signals.tpsl import estimate_cost


def _breakdown(entry=100_000.0, stop=99_800.0, maker=2.0, taker=5.0, notional=5_000.0, depth=2_000_000.0):
    return compute_cost_breakdown(
        entry_price=entry, stop_price=stop, fee_maker_bps=maker, fee_taker_bps=taker,
        notional_usd=notional, depth_usd=depth,
    )


def test_entry_and_tp_legs_use_maker_fee():
    c = _breakdown(entry=100_000.0, maker=2.0)
    assert c.entry_maker_fee == pytest.approx(100_000.0 * 2.0 / 10_000)
    assert c.tp_maker_fee == pytest.approx(100_000.0 * 2.0 / 10_000)


def test_sl_leg_uses_taker_fee_at_stop_price():
    c = _breakdown(stop=99_800.0, taker=5.0)
    assert c.sl_taker_fee == pytest.approx(99_800.0 * 5.0 / 10_000)


def test_round_trip_at_tp_is_two_times_maker():
    c = _breakdown(entry=100_000.0, maker=2.0, taker=5.0)
    assert c.round_trip_at_tp == pytest.approx(2 * 100_000.0 * 2.0 / 10_000)
    # and does NOT include any taker component
    assert c.round_trip_at_tp == pytest.approx(c.entry_maker_fee + c.tp_maker_fee)


def test_round_trip_at_sl_is_maker_plus_taker_plus_slippage():
    c = _breakdown()
    assert c.round_trip_at_sl == pytest.approx(c.entry_maker_fee + c.sl_taker_fee + c.sl_leg_slippage)


def test_round_trip_at_sl_exceeds_round_trip_at_tp_when_taker_above_maker():
    c = _breakdown(maker=2.0, taker=5.0)
    assert c.round_trip_at_sl > c.round_trip_at_tp


def test_zero_fees_give_zero_round_trips_apart_from_slippage():
    c = _breakdown(maker=0.0, taker=0.0, notional=0.0)
    assert c.round_trip_at_tp == 0.0
    assert c.sl_taker_fee == 0.0


def test_negative_fee_rejected():
    with pytest.raises(ValueError):
        _breakdown(maker=-1.0)


def test_non_positive_prices_rejected():
    with pytest.raises(ValueError):
        compute_cost_breakdown(entry_price=0, stop_price=1, fee_maker_bps=2, fee_taker_bps=5, notional_usd=1, depth_usd=1)


def test_rr_at_tp_long_uses_round_trip_at_tp_only():
    c = CostBreakdown(entry_maker_fee=1.0, tp_maker_fee=1.0, sl_taker_fee=99.0, sl_leg_slippage=99.0)
    rr = compute_rr_at_tp(direction=Direction.LONG, entry_price=100.0, stop_loss=90.0, take_profit=120.0, cost=c)
    # raw risk 10, raw reward 20, round trip at TP = 2.0 (the 99s must NOT leak in)
    assert rr == pytest.approx((20.0 - 2.0) / (10.0 + 2.0))


def test_rr_at_tp_short_symmetric():
    c = CostBreakdown(entry_maker_fee=1.0, tp_maker_fee=1.0, sl_taker_fee=99.0, sl_leg_slippage=99.0)
    rr = compute_rr_at_tp(direction=Direction.SHORT, entry_price=100.0, stop_loss=110.0, take_profit=80.0, cost=c)
    assert rr == pytest.approx((20.0 - 2.0) / (10.0 + 2.0))


def test_rr_at_tp_non_positive_risk_raises():
    c = CostBreakdown(1.0, 1.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        compute_rr_at_tp(direction=Direction.LONG, entry_price=100.0, stop_loss=100.0, take_profit=110.0, cost=c)


def test_cash_risk_long_is_stop_distance_plus_round_trip_at_sl():
    c = CostBreakdown(entry_maker_fee=1.0, tp_maker_fee=1.0, sl_taker_fee=3.0, sl_leg_slippage=0.5)
    risk = cash_risk_per_unit(direction=Direction.LONG, entry_price=100.0, stop_loss=90.0, cost=c)
    assert risk == pytest.approx(10.0 + (1.0 + 3.0 + 0.5))


def test_cash_risk_short():
    c = CostBreakdown(entry_maker_fee=1.0, tp_maker_fee=1.0, sl_taker_fee=3.0, sl_leg_slippage=0.5)
    risk = cash_risk_per_unit(direction=Direction.SHORT, entry_price=100.0, stop_loss=110.0, cost=c)
    assert risk == pytest.approx(10.0 + 4.5)


def test_cash_risk_non_positive_raises():
    c = CostBreakdown(1.0, 1.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        cash_risk_per_unit(direction=Direction.LONG, entry_price=100.0, stop_loss=101.0, cost=c)


def test_slippage_no_depth_returns_fallback():
    assert estimate_slippage_bps(1000.0, None) == 5.0
    assert estimate_slippage_bps(1000.0, 0) == 5.0


def test_slippage_scales_and_caps():
    assert estimate_slippage_bps(50_000.0, 100_000.0) > estimate_slippage_bps(1_000.0, 100_000.0)
    assert estimate_slippage_bps(10_000_000.0, 1_000.0) == 50.0


def test_estimate_cost_from_snapshot_uses_snapshot_fees_and_depth():
    ob = OrderBookState(symbol="BTCUSDT", best_bid=99.99, best_ask=100.01,
                        bid_depth_5lvl_usd=100_000, ask_depth_5lvl_usd=300_000,
                        event_ts_ms=1, received_ts_ms=1)
    snap = MarketSnapshot(snapshot_version="v", symbol="BTCUSDT", as_of_ts_ms=1, klines={},
                          orderbook=ob, taker_flow=None, derivatives=None, feed_health={},
                          price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=1.0, fee_taker_bps=4.0)
    c = estimate_cost(entry_price=100.0, stop_price=99.0, snapshot=snap, notional_usd=10_000.0)
    assert c.entry_maker_fee == pytest.approx(100.0 * 1.0 / 10_000)
    assert c.sl_taker_fee == pytest.approx(99.0 * 4.0 / 10_000)
    # depth used is min(bid, ask) = 100_000 -> ratio 0.1 -> 1 bp slippage at stop price
    assert c.sl_leg_slippage == pytest.approx(99.0 * 1.0 / 10_000)


def test_estimate_cost_without_orderbook_uses_fallback_slippage():
    snap = MarketSnapshot(snapshot_version="v", symbol="BTCUSDT", as_of_ts_ms=1, klines={},
                          orderbook=None, taker_flow=None, derivatives=None, feed_health={},
                          price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0)
    c = estimate_cost(entry_price=100.0, stop_price=99.0, snapshot=snap, notional_usd=1000.0)
    assert c.sl_leg_slippage == pytest.approx(99.0 * 5.0 / 10_000)


# ---------------------------------------------------------------------------
# Latency slippage (backtest-only extension, Batch 5A)
# ---------------------------------------------------------------------------


def test_latency_slippage_zero_at_zero_latency():
    from app.backtest.costs import estimate_latency_slippage_bps

    assert estimate_latency_slippage_bps(0.0) == 0.0


def test_latency_slippage_scales_linearly():
    from app.backtest.costs import estimate_latency_slippage_bps

    low = estimate_latency_slippage_bps(2.0)
    high = estimate_latency_slippage_bps(10.0)
    assert high == pytest.approx(low * 5)


def test_latency_slippage_capped_at_25_bps():
    from app.backtest.costs import estimate_latency_slippage_bps

    assert estimate_latency_slippage_bps(1000.0) == 25.0


def test_latency_slippage_negative_raises():
    from app.backtest.costs import estimate_latency_slippage_bps

    with pytest.raises(ValueError):
        estimate_latency_slippage_bps(-1.0)


def test_compute_cost_breakdown_default_latency_zero_matches_live_behavior():
    """Backward compatibility: omitting latency_s entirely (the live
    path's call signature) must produce entry_latency_slippage == 0.0,
    so round_trip_at_tp/round_trip_at_sl are numerically identical to
    pre-Batch-5A behavior."""
    c = compute_cost_breakdown(
        entry_price=100.0, stop_price=99.0, fee_maker_bps=2.0, fee_taker_bps=5.0,
        notional_usd=1000.0, depth_usd=100_000.0,
    )
    assert c.entry_latency_slippage == 0.0


def test_compute_cost_breakdown_with_latency_increases_round_trip_at_tp():
    c_no_latency = compute_cost_breakdown(
        entry_price=100.0, stop_price=99.0, fee_maker_bps=2.0, fee_taker_bps=5.0,
        notional_usd=1000.0, depth_usd=100_000.0, latency_s=0.0,
    )
    c_with_latency = compute_cost_breakdown(
        entry_price=100.0, stop_price=99.0, fee_maker_bps=2.0, fee_taker_bps=5.0,
        notional_usd=1000.0, depth_usd=100_000.0, latency_s=5.0,
    )
    assert c_with_latency.round_trip_at_tp > c_no_latency.round_trip_at_tp
    assert c_with_latency.round_trip_at_sl > c_no_latency.round_trip_at_sl
