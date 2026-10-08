from __future__ import annotations

import pytest

from app.config import load_all
from app.core.math import OHLC
from app.core.models import (
    DerivativesState,
    MarketSnapshot,
    NewsState,
    SymbolKlines,
    TakerFlowState,
    TimestampedValue,
)
from app.strategies.base import validate_meta_contract
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep

CFG = load_all().strategy


def _empty_news(as_of=1000):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _flat_bars(n, price, interval=300_000, start=0):
    return [
        OHLC(open=price, high=price + 5, low=price - 5, close=price, volume=100, close_time_ms=start + i * interval)
        for i in range(n)
    ]


def _base_snapshot(bars_5m, taker_flow, as_of_ts_ms):
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=as_of_ts_ms,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars_5m)},
        orderbook=None, taker_flow=taker_flow, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def test_s1_returns_empty_on_insufficient_history():
    bars = _flat_bars(10, 100.0)
    taker_flow = TakerFlowState(symbol="BTCUSDT", taker_buy_base_last_bars=[1], total_volume_last_bars=[2], event_ts_ms=1000, received_ts_ms=1000)
    snapshot = _base_snapshot(bars, taker_flow, 3_000_000)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert result == []


def test_s1_returns_empty_without_taker_flow():
    bars = _flat_bars(70, 100.0)
    snapshot = _base_snapshot(bars, None, bars[-1].close_time_ms + 1)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert result == []


def test_s1_returns_empty_with_stale_taker_flow():
    bars = _flat_bars(70, 100.0)
    as_of = bars[-1].close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[1], total_volume_last_bars=[2],
        event_ts_ms=0, received_ts_ms=0,  # far in the past relative to as_of
    )
    snapshot = _base_snapshot(bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert result == []


def test_s1_no_trigger_on_flat_market():
    bars = _flat_bars(70, 100.0)
    as_of = bars[-1].close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[1, 1, 1], total_volume_last_bars=[2, 2, 2],
        event_ts_ms=as_of, received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert result == []


def test_s1_triggers_long_on_engineered_sweep():
    base_price = 100.0
    bars = _flat_bars(65, base_price)
    swing_bars = [
        OHLC(open=base_price, high=base_price + 5, low=base_price - 5, close=base_price, volume=100, close_time_ms=bars[-1].close_time_ms + 300_000 * (i + 1))
        for i in range(3)
    ]
    prev_bar = OHLC(open=base_price, high=base_price + 5, low=base_price - 20, close=base_price - 2, volume=100, close_time_ms=swing_bars[-1].close_time_ms + 300_000)
    l_high = base_price + 5
    atr_approx = 10.0
    trigger_bar = OHLC(
        open=base_price, high=l_high + 0.5 * atr_approx, low=base_price - 3,
        close=l_high - 0.05 * atr_approx, volume=300, close_time_ms=prev_bar.close_time_ms + 300_000,
    )
    all_bars = bars + swing_bars + [prev_bar, trigger_bar]
    as_of = trigger_bar.close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[8, 8, 8], total_volume_last_bars=[10, 10, 10],
        event_ts_ms=as_of, received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(all_bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert len(result) >= 1
    candidate = result[0]
    assert candidate.direction.value == "LONG"
    assert candidate.strategy_source == "S1"
    assert candidate.stop_loss < candidate.entry_low < candidate.entry_high < candidate.tp1 < candidate.tp2 < candidate.tp3 < candidate.tp4


def test_s1_no_trigger_when_taker_flow_insufficient_for_long():
    base_price = 100.0
    bars = _flat_bars(65, base_price)
    swing_bars = [
        OHLC(open=base_price, high=base_price + 5, low=base_price - 5, close=base_price, volume=100, close_time_ms=bars[-1].close_time_ms + 300_000 * (i + 1))
        for i in range(3)
    ]
    prev_bar = OHLC(open=base_price, high=base_price + 5, low=base_price - 20, close=base_price - 2, volume=100, close_time_ms=swing_bars[-1].close_time_ms + 300_000)
    l_high = base_price + 5
    atr_approx = 10.0
    trigger_bar = OHLC(
        open=base_price, high=l_high + 0.5 * atr_approx, low=base_price - 3,
        close=l_high - 0.05 * atr_approx, volume=300, close_time_ms=prev_bar.close_time_ms + 300_000,
    )
    all_bars = bars + swing_bars + [prev_bar, trigger_bar]
    as_of = trigger_bar.close_time_ms + 1
    # Taker buy ratio well below the 0.55 threshold for LONG.
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[1, 1, 1], total_volume_last_bars=[10, 10, 10],
        event_ts_ms=as_of, received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(all_bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert result == []


def test_s1_candidate_meta_satisfies_base_contract():
    base_price = 100.0
    bars = _flat_bars(65, base_price)
    swing_bars = [
        OHLC(open=base_price, high=base_price + 5, low=base_price - 5, close=base_price, volume=100, close_time_ms=bars[-1].close_time_ms + 300_000 * (i + 1))
        for i in range(3)
    ]
    prev_bar = OHLC(open=base_price, high=base_price + 5, low=base_price - 20, close=base_price - 2, volume=100, close_time_ms=swing_bars[-1].close_time_ms + 300_000)
    l_high = base_price + 5
    atr_approx = 10.0
    trigger_bar = OHLC(
        open=base_price, high=l_high + 0.5 * atr_approx, low=base_price - 3,
        close=l_high - 0.05 * atr_approx, volume=300, close_time_ms=prev_bar.close_time_ms + 300_000,
    )
    all_bars = bars + swing_bars + [prev_bar, trigger_bar]
    as_of = trigger_bar.close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[8, 8, 8], total_volume_last_bars=[10, 10, 10],
        event_ts_ms=as_of, received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(all_bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert len(result) >= 1
    missing = validate_meta_contract(result[0])
    assert missing == []


def test_s1_tp_levels_are_exactly_k_times_r_from_entry_midpoint():
    """Regression test for the R-convention bug found in Batch 2/3
    review: S1 originally measured R from entry_low/entry_high while
    signal_engine graded R:R from the entry midpoint, silently making
    'TP2' something other than 2R as graded. TP_k must equal
    entry_mid + k*R exactly (LONG) or entry_mid - k*R exactly (SHORT),
    where R = |entry_mid - stop_loss|."""
    base_price = 100.0
    bars = _flat_bars(65, base_price)
    swing_bars = [
        OHLC(open=base_price, high=base_price + 5, low=base_price - 5, close=base_price, volume=100, close_time_ms=bars[-1].close_time_ms + 300_000 * (i + 1))
        for i in range(3)
    ]
    prev_bar = OHLC(open=base_price, high=base_price + 5, low=base_price - 20, close=base_price - 2, volume=100, close_time_ms=swing_bars[-1].close_time_ms + 300_000)
    l_high = base_price + 5
    atr_approx = 10.0
    trigger_bar = OHLC(
        open=base_price, high=l_high + 0.5 * atr_approx, low=base_price - 3,
        close=l_high - 0.05 * atr_approx, volume=300, close_time_ms=prev_bar.close_time_ms + 300_000,
    )
    all_bars = bars + swing_bars + [prev_bar, trigger_bar]
    as_of = trigger_bar.close_time_ms + 1
    taker_flow = TakerFlowState(
        symbol="BTCUSDT", taker_buy_base_last_bars=[8, 8, 8], total_volume_last_bars=[10, 10, 10],
        event_ts_ms=as_of, received_ts_ms=as_of,
    )
    snapshot = _base_snapshot(all_bars, taker_flow, as_of)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(), CFG)
    assert len(result) >= 1
    c = result[0]
    mid = (c.entry_low + c.entry_high) / 2
    r = abs(mid - c.stop_loss)
    multiples = CFG["s1_liquidity_sweep"]["tp_r_multiples"]
    tps = (c.tp1, c.tp2, c.tp3, c.tp4)
    sign = 1 if c.direction.value == "LONG" else -1
    for tp, m in zip(tps, multiples):
        assert tp == pytest.approx(mid + sign * m * r), (
            f"TP at multiple {m} is not exactly {m}R from entry midpoint"
        )
