"""Tests for S5 - OI Regime Shift.

FINDING (documented, not silently worked around): S5's tp_r_multiples
= [1.0, 2.0, 3.0, 4.0] caps raw (pre-cost) R:R at TP2 at exactly 2.0 by
construction. After the 2 x maker round-trip cost, clearing
min_rr_tp2 = 1.8 requires roughly ATR >= 93 bps (on a $100k BTC-scale
fixture, ATR ~ $930+, i.e. sl_buffer_atr=0.6 x that ATR for R) --
again an elevated-volatility regime, the same class of finding as S2's
sl_boundary_buffer_atr (see test_s2_volatility_compression.py). Not
fixed here: min_rr_tp2 (class F) and sl_buffer_atr (class F) are both
left untouched per the standing instruction. Flagged for shadow-mode /
threshold-review attention -- this is now the THIRD strategy (S1, S2,
S5) whose current class-F geometry needs an elevated-volatility regime
to economically clear the class-E cost-safety filters, which may
itself be worth review as a pattern rather than three separate
coincidences.
"""
from __future__ import annotations

from dataclasses import replace

from app.config import load_all
from app.core.math import OHLC, wilder_atr
from app.core.models import (
    ChannelName,
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
from app.strategies.s5_oi_regime import S5OiRegime

CFG = load_all().strategy
P = 100_000.0
IV = 300_000
NOW = 1_800_000_000_000


def _empty_news(as_of=NOW):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _flat(n, iv, price):
    s = NOW - (n + 1) * iv
    return [OHLC(open=price, high=price + 1, low=price - 1, close=price, volume=100.0, close_time_ms=s + i * iv) for i in range(n)]


def _elevated_vol_uptrend_bars(n5=120, half_base=900, half_jitter=410, step=200, final_step=300, final_wick=500):
    t0 = NOW - (n5 + 1) * IV
    bars, price = [], P
    for i in range(n5 - 1):
        half = half_base + (i * 37) % half_jitter
        c = price + step
        hi, lo = max(price, c) + half, min(price, c) - half
        bars.append(OHLC(open=price, high=hi, low=lo, close=c, volume=100, close_time_ms=t0 + i * IV))
        price = c
    c = price + final_step
    bars.append(OHLC(open=price, high=max(price, c) + final_wick, low=min(price, c) - final_wick, close=c, volume=100, close_time_ms=t0 + (n5 - 1) * IV))
    return bars


def _regime_shift_oi_series(as_of, n_oi=70, base=1_000_000.0, slow_step=200.0, accel_step=8000.0):
    """OI series with a slow rise for most of the window, then a sharp
    acceleration in the last regime_shift_window (12) bars -- the
    'regime shift' pattern S5 is designed to detect."""
    oi_vals = [base + i * slow_step for i in range(n_oi - 13)]
    last = oi_vals[-1]
    for j in range(13):
        oi_vals.append(last + (j + 1) * accel_step)
    return [
        TimestampedValue(value=v, event_ts_ms=as_of - 30_000 - (n_oi - 1 - i) * IV, received_ts_ms=as_of - 30_000 - (n_oi - 1 - i) * IV + 500)
        for i, v in enumerate(oi_vals)
    ], oi_vals


def _oi_1d_spanning(oi_vals, as_of, n_days=30):
    oi_min, oi_max = min(oi_vals), max(oi_vals)
    return [
        TimestampedValue(value=oi_min + (oi_max - oi_min) * i / (n_days - 1), event_ts_ms=as_of - (n_days - i) * 86_400_000, received_ts_ms=as_of)
        for i in range(n_days)
    ]


def _snapshot(bars_5m, deriv, taker_pair, as_of):
    last_price = bars_5m[-1].close
    ob = OrderBookState(symbol="BTCUSDT", best_bid=last_price - 0.05, best_ask=last_price + 0.05,
                        bid_depth_5lvl_usd=2_000_000, ask_depth_5lvl_usd=2_000_000, event_ts_ms=as_of, received_ts_ms=as_of)
    buy, total = taker_pair
    tf = TakerFlowState(symbol="BTCUSDT", taker_buy_base_last_bars=[buy, buy, buy], total_volume_last_bars=[total, total, total],
                        event_ts_ms=as_of, received_ts_ms=as_of)
    fh = {"btcusdt@aggTrade": FeedHealth(symbol="BTCUSDT", stream="btcusdt@aggTrade", last_message_received_ts_ms=as_of - 500, reconnect_count_window=0, is_connected=True)}
    return MarketSnapshot(
        snapshot_version="s5test", symbol="BTCUSDT", as_of_ts_ms=as_of,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars_5m),
                "15m": SymbolKlines(symbol="BTCUSDT", timeframe="15m", bars=_flat(30, 900_000, last_price)),
                "1h": SymbolKlines(symbol="BTCUSDT", timeframe="1h", bars=_flat(30, 3_600_000, last_price)),
                "4h": SymbolKlines(symbol="BTCUSDT", timeframe="4h", bars=_flat(30, 14_400_000, last_price))},
        orderbook=ob, taker_flow=tf, derivatives=deriv, feed_health=fh,
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def build_realistic_s5_long_snapshot(funding_value=0.0001):
    bars_5m = _elevated_vol_uptrend_bars()
    as_of = bars_5m[-1].close_time_ms + 1000
    oi5, oi_vals = _regime_shift_oi_series(as_of)
    oi_1d = _oi_1d_spanning(oi_vals, as_of)
    funding_hist = [TimestampedValue(value=funding_value, event_ts_ms=as_of - i * 1000, received_ts_ms=as_of - i * 1000) for i in range(20)]
    deriv = DerivativesState(
        symbol="BTCUSDT", funding_rate_history=funding_hist, open_interest_history_5m=oi5,
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None,
    )
    return _snapshot(bars_5m, deriv, (8, 10), as_of)  # buy_ratio 0.8 > 0.5, aligned with LONG (price_up_oi_up)


def test_s5_returns_empty_without_derivatives():
    snap = build_realistic_s5_long_snapshot()
    snap = replace(snap, derivatives=None)
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s5_returns_empty_on_insufficient_5m_oi_points():
    bars_5m = _elevated_vol_uptrend_bars()
    as_of = bars_5m[-1].close_time_ms + 1000
    short_oi = [TimestampedValue(value=1_000_000.0, event_ts_ms=as_of - 1000, received_ts_ms=as_of - 1000)]
    deriv = DerivativesState(symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=short_oi,
                             open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=[],
                             long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None)
    snap = _snapshot(bars_5m, deriv, (8, 10), as_of)
    result = S5OiRegime().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s5_returns_empty_with_stale_oi():
    snap = build_realistic_s5_long_snapshot()
    stale = [replace(tv, event_ts_ms=tv.event_ts_ms - 10_000_000, received_ts_ms=tv.received_ts_ms - 10_000_000) for tv in snap.derivatives.open_interest_history_5m]
    snap = replace(snap, derivatives=replace(snap.derivatives, open_interest_history_5m=stale))
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s5_returns_empty_on_insufficient_1d_history():
    snap = build_realistic_s5_long_snapshot()
    snap = replace(snap, derivatives=replace(snap.derivatives, open_interest_history_1d=snap.derivatives.open_interest_history_1d[:5]))
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s5_returns_empty_without_taker_flow():
    snap = build_realistic_s5_long_snapshot()
    snap = replace(snap, taker_flow=None)
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s5_noise_band_rejects_small_oi_delta():
    """Regression test (required): an OI delta smaller than
    oi_noise_band must be treated as noise -> NO TRADE, even if a
    'regime shift' percentile crossing were otherwise present."""
    bars_5m = _elevated_vol_uptrend_bars()
    as_of = bars_5m[-1].close_time_ms + 1000
    # Tiny final-window acceleration: delta well under the 0.5% noise band.
    oi5, oi_vals = _regime_shift_oi_series(as_of, accel_step=10.0)
    oi_1d = _oi_1d_spanning(oi_vals, as_of)
    deriv = DerivativesState(symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi5,
                             open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
                             long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None)
    snap = _snapshot(bars_5m, deriv, (8, 10), as_of)
    oi_delta_5m = (oi5[-1].value - oi5[-2].value) / oi5[-2].value
    assert abs(oi_delta_5m) < CFG["s5_oi_regime"]["oi_noise_band"], "fixture must actually be within the noise band"
    result = S5OiRegime().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s5_no_trigger_without_regime_shift_crossing():
    """OI delta is meaningful but there's no percentile crossing from
    below regime_low to above regime_high (OI stays mid-range
    throughout) -> NO TRADE."""
    bars_5m = _elevated_vol_uptrend_bars()
    as_of = bars_5m[-1].close_time_ms + 1000
    oi5, oi_vals = _regime_shift_oi_series(as_of, accel_step=8000.0)
    # 1d history deliberately spans a MUCH wider range so nothing in
    # the 5m series reaches the top-20%/bottom-20% percentile bands.
    wide_min, wide_max = min(oi_vals) - 5_000_000, max(oi_vals) + 5_000_000
    oi_1d = [TimestampedValue(value=wide_min + (wide_max - wide_min) * i / 29, event_ts_ms=as_of - (30 - i) * 86_400_000, received_ts_ms=as_of) for i in range(30)]
    deriv = DerivativesState(symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi5,
                             open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
                             long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None)
    snap = _snapshot(bars_5m, deriv, (8, 10), as_of)
    result = S5OiRegime().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s5_no_trigger_on_weak_quadrant():
    """price_up_oi_down (short covering) is explicitly a weak quadrant,
    never a standalone signal."""
    bars_5m = _elevated_vol_uptrend_bars()  # price rising
    as_of = bars_5m[-1].close_time_ms + 1000
    # OI series where the final-window OI FALLS instead of rising.
    oi_vals = [1_000_000 + i * 200 for i in range(70 - 13)]
    last = oi_vals[-1]
    for j in range(13):
        oi_vals.append(last - (j + 1) * 8000)  # falling
    oi5 = [TimestampedValue(value=v, event_ts_ms=as_of - 30_000 - (69 - i) * IV, received_ts_ms=as_of - 30_000 - (69 - i) * IV + 500) for i, v in enumerate(oi_vals)]
    oi_1d = _oi_1d_spanning(oi_vals, as_of)
    deriv = DerivativesState(symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi5,
                             open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
                             long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None)
    snap = _snapshot(bars_5m, deriv, (8, 10), as_of)
    result = S5OiRegime().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s5_triggers_long_on_realistic_regime_shift():
    snap = build_realistic_s5_long_snapshot()
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert c.direction.value == "LONG"
    assert c.strategy_source == "S5"
    assert c.stop_loss < c.entry_low <= c.entry_high < c.tp1 < c.tp2 < c.tp3 < c.tp4
    assert validate_meta_contract(c) == []


def test_s5_candidate_clears_min_rr_tp2_gate():
    snap = build_realistic_s5_long_snapshot()
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    from app.backtest.costs import compute_cost_breakdown, compute_rr_at_tp

    c = result[0]
    mid = (c.entry_low + c.entry_high) / 2
    cost = compute_cost_breakdown(entry_price=mid, stop_price=c.stop_loss, fee_maker_bps=2.0, fee_taker_bps=5.0,
                                  notional_usd=mid, depth_usd=2_000_000)
    rr = compute_rr_at_tp(direction=c.direction, entry_price=mid, stop_loss=c.stop_loss, take_profit=c.tp2, cost=cost)
    assert rr >= CFG["s5_oi_regime"]["min_rr_tp2"]


def test_s5_funding_veto_blocks_opposing_extreme_funding():
    """Funding extreme in the OPPOSITE direction of the candidate must
    veto it (funding is veto-only here, never a positive vote).

    S5 (like S3) treats funding_values[-1] as "current" -- the LAST
    (chronologically most recent) element of the ordered series. An
    earlier version of this fixture placed the extreme value at index
    0 while building timestamps as `as_of - i*1000` (so index 0 had the
    NEWEST timestamp without an explicit sort) -- meaning the "current"
    value the code actually read (index -1) was the ordinary 0.0001,
    not the extreme -0.05, and the veto never had a chance to fire.
    Fixed by placing the extreme value chronologically last, matching
    how build_derivatives_state's real ordering works (oldest-first).
    """
    snap = build_realistic_s5_long_snapshot()
    n = 20
    # Oldest-first ordering; the extreme opposing value is the LAST
    # (most recent) element, which is what funding_values[-1] reads.
    opposing_funding = [
        TimestampedValue(
            value=0.0001 if i < n - 1 else -0.05,
            event_ts_ms=snap.as_of_ts_ms - (n - 1 - i) * 1000,
            received_ts_ms=snap.as_of_ts_ms - (n - 1 - i) * 1000,
        )
        for i in range(n)
    ]
    snap2 = replace(snap, derivatives=replace(snap.derivatives, funding_rate_history=opposing_funding))
    result = S5OiRegime().evaluate(snap2, _empty_news(snap2.as_of_ts_ms), CFG)
    assert result == [], "opposing funding extreme must veto the S5 candidate"


def test_s5_funding_veto_does_not_block_aligned_or_neutral_funding():
    """Baseline (near-zero, non-opposing) funding must not veto."""
    snap = build_realistic_s5_long_snapshot(funding_value=0.0001)
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Double-counting: OI + taker flow, funding excluded from channels
# ---------------------------------------------------------------------------


def test_s5_channels_are_oi_and_taker_flow_only():
    """Funding is veto-only and must NOT appear in channels (spec
    section 11 / module docstring): only OI and TAKER_FLOW."""
    snap = build_realistic_s5_long_snapshot()
    result = S5OiRegime().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert set(c.channels) == {ChannelName.OI, ChannelName.TAKER_FLOW}
    assert ChannelName.FUNDING not in c.channels


def test_s5_registry_channels_match_strategy_output():
    from app.risk.channels import channels_for_strategy

    assert set(channels_for_strategy("S5")) == {ChannelName.OI, ChannelName.TAKER_FLOW}
