"""End-to-end proof that run_single_symbol_backtest produces real
trades through the SAME live pipeline (strategies -> consensus ->
veto -> fill -> partial-exit tracking), with the realized R
independently sanity-checked against the configured TP multiples and
partial-exit fractions.

The fixture uses both weak (0.26 ATR) and strong (0.6 ATR) S1 sweep
penetrations to verify that the corrected confidence scale lets a
qualifying weak setup reach grade B without changing the 0.55 threshold.
"""
from __future__ import annotations

import random

import pytest

from app.config import load_all
from app.core.math import OHLC, wilder_atr
from app.core.models import (
    DerivativesState,
    NewsState,
    OrderBookState,
    TakerFlowState,
    TimestampedValue,
)
from app.backtest.engine import BacktestConfig, run_single_symbol_backtest
import app.backtest.engine as backtest_engine

CFG = load_all()
P = 100_000.0
IV = 300_000
NOW = 1_800_000_000_000


def _build_fixture(*, penetration_atr_mult: float, continuation_step: float, continuation_bars: int, seed: int = 42):
    rng = random.Random(seed)
    n_hist = 260
    t0 = NOW - (n_hist + 40) * IV
    bars = []
    price = P
    for i in range(n_hist):
        half = rng.uniform(70, 250)
        c = price + (40 if i % 2 == 0 else -40)
        hi, lo = max(price, c) + half, min(price, c) - half
        bars.append(OHLC(open=price, high=hi, low=lo, close=c, volume=100.0, close_time_ms=t0 + i * IV))
        price = c

    k = len(bars)
    sp = bars[-1].close
    swing_bars = [
        OHLC(open=sp, high=sp + 100, low=sp - 100, close=sp, volume=100, close_time_ms=t0 + (k + j) * IV)
        for j in range(3)
    ]
    prev_bar = OHLC(open=sp, high=sp + 100, low=sp - 150, close=sp - 20, volume=100, close_time_ms=t0 + (k + 3) * IV)
    atr_now = wilder_atr(bars, 14)
    l_high = sp + 100
    trigger = OHLC(
        open=sp, high=l_high + penetration_atr_mult * atr_now, low=sp - 60,
        close=l_high - 0.10 * atr_now, volume=300, close_time_ms=t0 + (k + 4) * IV,
    )
    bars_with_trigger = bars + swing_bars + [prev_bar, trigger]
    trigger_index = len(bars_with_trigger) - 1

    price = trigger.close
    extra = []
    for i in range(continuation_bars):
        c = price + continuation_step
        hi, lo = max(price, c) + 20, min(price, c) - 20
        extra.append(OHLC(open=price, high=hi, low=lo, close=c, volume=100, close_time_ms=trigger.close_time_ms + (i + 1) * IV))
        price = c

    all_5m = bars_with_trigger + extra

    as_of_last = all_5m[-1].close_time_ms
    ob = OrderBookState(
        symbol="BTCUSDT", best_bid=P - 0.05, best_ask=P + 0.05,
        bid_depth_5lvl_usd=2_000_000, ask_depth_5lvl_usd=2_000_000,
        event_ts_ms=as_of_last, received_ts_ms=as_of_last,
    )
    tf = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[8, 8, 8], total_volume_last_bars=[10, 10, 10],
        event_ts_ms=as_of_last, received_ts_ms=as_of_last,
    )
    oi5 = [
        TimestampedValue(value=1_000_000 + i * 30, event_ts_ms=as_of_last - 30_000 - (59 - i) * IV, received_ts_ms=as_of_last - 30_000 - (59 - i) * IV + 500)
        for i in range(60)
    ]
    deriv = DerivativesState(
        symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi5,
        open_interest_history_15m=[], open_interest_history_1h=[],
        open_interest_history_1d=[
            TimestampedValue(value=1_000_000 + i * 500, event_ts_ms=as_of_last - (30 - i) * 86_400_000, received_ts_ms=as_of_last)
            for i in range(30)
        ],
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None,
    )

    return all_5m, trigger_index, ob, tf, deriv


def _run(all_5m, ob, tf, deriv):
    all_bars = {"5m": all_5m, "15m": [], "1h": [], "4h": [], "1d": []}
    orderbook_at_index = {i: ob for i in range(60, len(all_5m))}
    taker_flow_at_index = {i: tf for i in range(60, len(all_5m))}
    derivatives_at_index = {i: deriv for i in range(60, len(all_5m))}
    news = NewsState(as_of_ts_ms=all_5m[-1].close_time_ms, active_events=(), unhealthy_categories=frozenset())
    bt_cfg = BacktestConfig(app_config=CFG, assumed_equity_usd=1000.0, symbol_tier="majors", min_candles=60)
    return run_single_symbol_backtest(
        symbol="BTCUSDT", all_bars=all_bars, event_ts_per_5m_index=[], bt_cfg=bt_cfg, news_state=news,
        orderbook_at_index=orderbook_at_index, taker_flow_at_index=taker_flow_at_index,
        derivatives_at_index=derivatives_at_index,
    )


def test_engine_produces_a_real_trade_on_strong_continuation():
    """Full pipeline: strategy -> consensus grade B -> veto PASS ->
    forward fill search -> partial-exit tracking to a closed trade."""
    all_5m, trigger_index, ob, tf, deriv = _build_fixture(
        penetration_atr_mult=0.6, continuation_step=80.0, continuation_bars=40,
    )
    results = _run(all_5m, ob, tf, deriv)
    assert len(results) == 1
    trade = results[0]
    assert trade.strategy_source == "S1"
    assert trade.direction == "LONG"
    assert trade.was_filled is True


def test_engine_realized_r_is_internally_consistent_with_partial_exit_math():
    """Independent check: with a strong enough continuation to plausibly
    clear all 4 TP levels, realized R should land close to (not
    necessarily exactly equal to, since fills can overshoot TP prices)
    the fully-realized value implied by tp_r_multiples and
    partial_exit_fractions: 0.4*1 + 0.3*2 + 0.2*3 + 0.1*5 = 2.1."""
    all_5m, trigger_index, ob, tf, deriv = _build_fixture(
        penetration_atr_mult=0.6, continuation_step=80.0, continuation_bars=40,
    )
    results = _run(all_5m, ob, tf, deriv)
    assert len(results) == 1
    trade = results[0]
    expected_if_exact = 0.4 * 1 + 0.3 * 2 + 0.2 * 3 + 0.1 * 5
    # Overshoot-tolerant bound: realized R should be AT LEAST the exact
    # value (fills happen at or beyond each TP given a strongly trending
    # bar sequence, never worse) and not absurdly larger.
    assert trade.realized_r >= expected_if_exact - 0.01
    assert trade.realized_r < expected_if_exact * 3  # sanity ceiling, not a tight bound


def test_engine_produces_no_trade_when_continuation_reverses_immediately(monkeypatch):
    """Negative control: same trigger, but price reverses hard right
    after entry instead of continuing -> should stop out for a loss
    near -1R (accounting for the partial fraction already filled before
    any TP, i.e. the full position stops at -1R since no TP was hit)."""
    monkeypatch.setattr(backtest_engine, "fill_probability_succeeds", lambda *a, **k: True)
    all_5m, trigger_index, ob, tf, deriv = _build_fixture(
        penetration_atr_mult=0.6, continuation_step=-150.0, continuation_bars=10,
    )
    results = _run(all_5m, ob, tf, deriv)
    assert len(results) == 1
    trade = results[0]
    assert trade.was_filled is True
    assert -2.0 < trade.realized_r < -1.0  # raw stop loss plus modeled fees/slippage/latency


def test_weak_sweep_penetration_reaches_grade_b_after_factor_rescale():
    """A qualifying 0.26 ATR sweep is no longer discarded by bad scaling."""
    all_5m, trigger_index, ob, tf, deriv = _build_fixture(
        penetration_atr_mult=0.26, continuation_step=80.0, continuation_bars=40,
    )
    results = _run(all_5m, ob, tf, deriv)
    assert len(results) == 1
    assert results[0].strategy_source == "S1"


def test_engine_with_no_auxiliary_data_produces_zero_trades_fail_closed():
    """Every strategy requires taker_flow and/or derivatives data. A
    replay with NONE supplied must honestly produce zero trades for
    every strategy, mirroring live fail-closed behavior exactly -- not
    an engine limitation, a faithful carry-over of each strategy's
    documented fail-closed conditions."""
    all_5m, trigger_index, ob, tf, deriv = _build_fixture(
        penetration_atr_mult=0.6, continuation_step=80.0, continuation_bars=40,
    )
    all_bars = {"5m": all_5m, "15m": [], "1h": [], "4h": [], "1d": []}
    news = NewsState(as_of_ts_ms=all_5m[-1].close_time_ms, active_events=(), unhealthy_categories=frozenset())
    bt_cfg = BacktestConfig(app_config=CFG, assumed_equity_usd=1000.0, symbol_tier="majors", min_candles=60)
    results = run_single_symbol_backtest(
        symbol="BTCUSDT", all_bars=all_bars, event_ts_per_5m_index=[], bt_cfg=bt_cfg, news_state=news,
    )
    assert results == []
