"""Tests for S2 - Volatility Compression -> Range Expansion.

FINDING (documented, not silently worked around): S2's
sl_boundary_buffer_atr = 0.1 (class F) produces a stop distance of only
0.1x ATR. Against the min_stop_cost_multiple = 3.0 filter (class E),
this means S2's stop only clears the safety filter when ATR itself is
large relative to price -- in this fixture, ATR ~ 136 bps (a materially
elevated-volatility regime), versus the ~20 bps ATR used in the
Batch 2/3 realistic S1 fixture. At a typical/lower-volatility ATR, S2's
stop is arithmetically too tight to survive the cost-safety filter
regardless of how clean the rest of the setup is. This is a property
of the current sl_boundary_buffer_atr value interacting with the fixed
0.1-ATR stop construction, not a fixture artifact: the same 0.1x
multiple applies to any market. Not fixed here -- min_stop_cost_multiple
and sl_boundary_buffer_atr are class E/F respectively and neither is
tuned per the standing instruction. Flagged for shadow-mode /
threshold-review attention.
"""
from __future__ import annotations

import pytest

from app.config import load_all
from app.core.math import OHLC, percentile_rank, rolling_median, wilder_atr, wilder_atr_series
from app.core.models import (
    DerivativesState,
    FeedHealth,
    MarketSnapshot,
    NewsState,
    OrderBookState,
    SymbolKlines,
    TakerFlowState,
    TimestampedValue,
)
from app.strategies.base import validate_meta_contract
from app.strategies.s2_volatility_compression import S2VolatilityCompression

CFG = load_all().strategy
P = 100_000.0
IV = 300_000
NOW = 1_800_000_000_000


def _empty_news(as_of=NOW):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _flat(n, iv, price=P):
    s = NOW - (n + 1) * iv
    return [OHLC(open=price, high=price + 1, low=price - 1, close=price, volume=100.0, close_time_ms=s + i * iv) for i in range(n)]


def _snapshot(bars_5m, oi_5m, as_of, *, orderbook_depth=2_000_000.0):
    ob = OrderBookState(symbol="BTCUSDT", best_bid=P - 0.05, best_ask=P + 0.05,
                        bid_depth_5lvl_usd=orderbook_depth, ask_depth_5lvl_usd=orderbook_depth,
                        event_ts_ms=as_of, received_ts_ms=as_of)
    tf = TakerFlowState(symbol="BTCUSDT", taker_buy_base_last_bars=[8, 8, 8], total_volume_last_bars=[10, 10, 10],
                        event_ts_ms=as_of, received_ts_ms=as_of)
    deriv = DerivativesState(symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi_5m,
                             open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=[],
                             long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None)
    fh = {"btcusdt@aggTrade": FeedHealth(symbol="BTCUSDT", stream="btcusdt@aggTrade",
                                         last_message_received_ts_ms=as_of - 500, reconnect_count_window=0, is_connected=True)}
    return MarketSnapshot(
        snapshot_version="s2test", symbol="BTCUSDT", as_of_ts_ms=as_of,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars_5m),
                "15m": SymbolKlines(symbol="BTCUSDT", timeframe="15m", bars=_flat(30, 900_000)),
                "1h": SymbolKlines(symbol="BTCUSDT", timeframe="1h", bars=_flat(30, 3_600_000)),
                "4h": SymbolKlines(symbol="BTCUSDT", timeframe="4h", bars=_flat(30, 14_400_000))},
        orderbook=ob, taker_flow=tf, derivatives=deriv, feed_health=fh,
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def _compression_history(n_hist=400, *, pre_half_base=9000, pre_half_jitter=3610, pre_drift=4500,
                          compress_half_amp=2200, compress_step=300, compress_wick=450):
    """Elevated-volatility-regime compression history: a wide-range
    pre-compression phase feeding ATR(14), then a 60-bar compression
    window whose range collapses relative to that ATR, ending with a
    breakout bar. See module docstring for why the absolute scale here
    (not just the compression RATIO) matters for this strategy given
    its current sl_boundary_buffer_atr."""
    t0 = NOW - (n_hist + 5) * IV
    bars = []
    for i in range(n_hist - 60):
        half = pre_half_base + (i * 37) % pre_half_jitter
        c = P + (pre_drift if i % 2 == 0 else -pre_drift)
        bars.append(OHLC(open=P, high=P + half, low=P - half, close=c, volume=100.0, close_time_ms=t0 + i * IV))
    k = len(bars)
    compressed_high, compressed_low = P + compress_half_amp, P - compress_half_amp
    for j in range(60):
        o = P
        c = P + (compress_step if j % 2 == 0 else -compress_step)
        bars.append(OHLC(
            open=o, high=min(compressed_high, max(o, c) + compress_wick),
            low=max(compressed_low, min(o, c) - compress_wick), close=c, volume=50.0,
            close_time_ms=t0 + (k + j) * IV,
        ))
    return t0, k, bars


def _oi_series(as_of, *, base=1_000_000.0, step=30.0, n=60, jump_to=1_020_000.0):
    newest = as_of - 30_000
    series = [
        TimestampedValue(value=base + i * step, event_ts_ms=newest - (n - 1 - i) * IV, received_ts_ms=newest - (n - 1 - i) * IV + 500)
        for i in range(n - 1)
    ]
    series.append(TimestampedValue(value=jump_to, event_ts_ms=newest, received_ts_ms=newest + 500))
    return series


def build_realistic_s2_long_snapshot():
    t0, k, bars = _compression_history()
    atr_before = wilder_atr(bars, 14)
    range_high = max(b.high for b in bars[-60:])
    med_vol = rolling_median([b.volume for b in bars[-60:]])
    breakout_body = 2.0 * atr_before
    breakout = OHLC(open=P, high=range_high + breakout_body + 50, low=P - 20, close=P + breakout_body,
                    volume=med_vol * 2.0, close_time_ms=t0 + (k + 60) * IV)
    bars = bars + [breakout]
    as_of = bars[-1].close_time_ms + 1000
    return _snapshot(bars, _oi_series(as_of), as_of)


def build_no_compression_snapshot():
    """No compression at all (uniformly wide range): S2 must not trigger."""
    bars_5m = _flat(400, IV)
    as_of = bars_5m[-1].close_time_ms + 1000
    return _snapshot(bars_5m, _oi_series(as_of), as_of)


def test_s2_returns_empty_on_insufficient_history():
    bars = _flat(100, IV)
    as_of = bars[-1].close_time_ms + 1
    snap = _snapshot(bars, _oi_series(as_of, n=10), as_of)
    result = S2VolatilityCompression().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s2_returns_empty_without_derivatives():
    t0, k, bars = _compression_history()
    as_of = bars[-1].close_time_ms + 1000
    snap = _snapshot(bars, _oi_series(as_of), as_of)
    snap = snap.__class__(**{**snap.__dict__, "derivatives": None})
    result = S2VolatilityCompression().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s2_returns_empty_with_stale_oi():
    snap = build_realistic_s2_long_snapshot()
    stale_series = [
        TimestampedValue(value=tv.value, event_ts_ms=tv.event_ts_ms - 10_000_000, received_ts_ms=tv.received_ts_ms - 10_000_000)
        for tv in snap.derivatives.open_interest_history_5m
    ]
    from dataclasses import replace
    stale_deriv = replace(snap.derivatives, open_interest_history_5m=stale_series)
    stale_snap = replace(snap, derivatives=stale_deriv)
    result = S2VolatilityCompression().evaluate(stale_snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s2_no_trigger_without_compression():
    snap = build_no_compression_snapshot()
    result = S2VolatilityCompression().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s2_triggers_long_on_realistic_compression_breakout():
    snap = build_realistic_s2_long_snapshot()
    result = S2VolatilityCompression().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert c.direction.value == "LONG"
    assert c.strategy_source == "S2"
    assert c.stop_loss < c.entry_low <= c.entry_high < c.tp1 < c.tp2 < c.tp3 < c.tp4
    assert validate_meta_contract(c) == []


def test_s2_candidate_survives_min_stop_cost_filter_with_documented_margin():
    """Confirms the fixture's stop distance clears min_stop_cost_multiple
    with some margin, and records the ATR level required to do so (see
    module docstring FINDING)."""
    snap = build_realistic_s2_long_snapshot()
    result = S2VolatilityCompression().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    mid = (c.entry_low + c.entry_high) / 2
    r = abs(mid - c.stop_loss)
    round_trip = 2 * mid * snap.fee_maker_bps / 1e4
    multiple = r / round_trip
    assert multiple >= CFG["common"]["min_stop_cost_multiple"]


def test_s2_no_trigger_on_insufficient_volume_confirmation():
    """Same compression setup, but the breakout bar's volume is only
    at the median (no surge) -> volume_confirm_ratio fails."""
    t0, k, bars = _compression_history()
    atr_before = wilder_atr(bars, 14)
    range_high = max(b.high for b in bars[-60:])
    med_vol = rolling_median([b.volume for b in bars[-60:]])
    breakout_body = 2.0 * atr_before
    weak_volume_breakout = OHLC(
        open=P, high=range_high + breakout_body + 50, low=P - 20, close=P + breakout_body,
        volume=med_vol * 1.0,  # no surge
        close_time_ms=t0 + (k + 60) * IV,
    )
    bars = bars + [weak_volume_breakout]
    as_of = bars[-1].close_time_ms + 1000
    snap = _snapshot(bars, _oi_series(as_of), as_of)
    result = S2VolatilityCompression().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s2_no_trigger_on_insufficient_oi_confirmation():
    t0, k, bars = _compression_history()
    atr_before = wilder_atr(bars, 14)
    range_high = max(b.high for b in bars[-60:])
    med_vol = rolling_median([b.volume for b in bars[-60:]])
    breakout_body = 2.0 * atr_before
    breakout = OHLC(open=P, high=range_high + breakout_body + 50, low=P - 20, close=P + breakout_body,
                    volume=med_vol * 2.0, close_time_ms=t0 + (k + 60) * IV)
    bars = bars + [breakout]
    as_of = bars[-1].close_time_ms + 1000
    flat_oi = _oi_series(as_of, jump_to=1_000_030.0)  # negligible OI change
    snap = _snapshot(bars, flat_oi, as_of)
    result = S2VolatilityCompression().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s2_invalid_entry_mode_raises_valueerror():
    snap = build_realistic_s2_long_snapshot()
    bad_cfg = {**CFG, "s2_volatility_compression": {**CFG["s2_volatility_compression"], "entry_mode": "bogus"}}
    with pytest.raises(ValueError):
        S2VolatilityCompression().evaluate(snap, _empty_news(snap.as_of_ts_ms), bad_cfg)
