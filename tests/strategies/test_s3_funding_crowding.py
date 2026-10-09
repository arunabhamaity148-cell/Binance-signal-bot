"""Tests for S3 - Funding / Crowding Exhaustion Reversal.

Includes the hard-rule regression test (never signal from funding
alone) and the channel double-counting regression (funding+OI+ratios
collapse to exactly two canonical channels: FUNDING, OI).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import load_all
from app.core.math import OHLC, swing_highs_lows, wilder_atr
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
from app.strategies.s3_funding_crowding import S3FundingCrowding

CFG = load_all().strategy
P = 100_000.0
IV_5M = 300_000
IV_15M = 900_000
NOW = 1_800_000_000_000


def _empty_news(as_of=NOW):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _flat(n, iv, price):
    s = NOW - (n + 1) * iv
    return [OHLC(open=price, high=price + 1, low=price - 1, close=price, volume=100.0, close_time_ms=s + i * iv) for i in range(n)]


def _bleed_5m_bars(n5=120, start_price=P + 3000, step=25):
    t0 = NOW - (n5 + 1) * IV_5M
    bars, price = [], start_price
    for i in range(n5):
        half = 80 + (i * 37) % 41
        c = price - step
        hi, lo = max(price, c) + half, min(price, c) - half
        bars.append(OHLC(open=price, high=hi, low=lo, close=c, volume=100.0, close_time_ms=t0 + i * IV_5M))
        price = c
    return bars


def _swing_low_15m_bars(n15=40, start_price=P + 3000):
    """Zigzag ending in a confirmed swing low, then a final bar that
    closes BELOW that swing low (a structure break down)."""
    t0 = NOW - (n15 + 1) * IV_15M
    bars, p = [], start_price
    for i in range(n15 - 5):
        c = p - 20
        hi, lo = max(p, c) + 30, min(p, c) - 30
        bars.append(OHLC(open=p, high=hi, low=lo, close=c, volume=50, close_time_ms=t0 + i * IV_15M))
        p = c
    swing_low_price = p - 40
    bars.append(OHLC(open=p, high=p + 10, low=swing_low_price, close=p - 10, volume=50, close_time_ms=t0 + (n15 - 5) * IV_15M))
    p = p - 10
    for j in range(3):
        c = p + 30
        hi, lo = max(p, c) + 10, min(p, c) - 10
        bars.append(OHLC(open=p, high=hi, low=lo, close=c, volume=50, close_time_ms=t0 + (n15 - 4 + j) * IV_15M))
        p = c
    final_close = swing_low_price - 50
    bars.append(OHLC(open=p, high=p + 10, low=final_close - 10, close=final_close, volume=100, close_time_ms=t0 + n15 * IV_15M))
    return bars, final_close


def _swing_high_15m_bars(n15=40, start_price=P - 3000):
    """Mirror of _swing_low_15m_bars: zigzag ending in a confirmed
    swing high, then a final bar that closes ABOVE it (structure break
    up, for the shorts-crowded LONG-reversal case)."""
    t0 = NOW - (n15 + 1) * IV_15M
    bars, p = [], start_price
    for i in range(n15 - 5):
        c = p + 20
        hi, lo = max(p, c) + 30, min(p, c) - 30
        bars.append(OHLC(open=p, high=hi, low=lo, close=c, volume=50, close_time_ms=t0 + i * IV_15M))
        p = c
    swing_high_price = p + 40
    bars.append(OHLC(open=p, high=swing_high_price, low=p - 10, close=p + 10, volume=50, close_time_ms=t0 + (n15 - 5) * IV_15M))
    p = p + 10
    for j in range(3):
        c = p - 30
        hi, lo = max(p, c) + 10, min(p, c) - 10
        bars.append(OHLC(open=p, high=hi, low=lo, close=c, volume=50, close_time_ms=t0 + (n15 - 4 + j) * IV_15M))
        p = c
    final_close = swing_high_price + 50
    bars.append(OHLC(open=p, high=final_close + 10, low=p - 10, close=final_close, volume=100, close_time_ms=t0 + n15 * IV_15M))
    return bars, final_close


def _derivatives_longs_crowded(as_of):
    n_funding = 100
    funding_vals = [0.0001] * (n_funding - 1) + [0.006]
    funding_hist = [TimestampedValue(value=v, event_ts_ms=as_of - (n_funding - i) * 1000, received_ts_ms=as_of - (n_funding - i) * 1000) for i, v in enumerate(funding_vals)]
    oi_1d = [TimestampedValue(value=1_000_000 + i * 1000, event_ts_ms=as_of - (30 - i) * 86_400_000, received_ts_ms=as_of) for i in range(30)]
    oi_5m = [TimestampedValue(value=1_000_000 + i * 30, event_ts_ms=as_of - 30_000 - (59 - i) * IV_5M, received_ts_ms=as_of - 30_000 - (59 - i) * IV_5M + 500) for i in range(59)]
    oi_5m.append(TimestampedValue(value=1_050_000, event_ts_ms=as_of - 30_000, received_ts_ms=as_of - 30_000 + 500))
    ls_hist_vals = [1.0 + i * 0.01 for i in range(40)]
    ls_hist = [TimestampedValue(value=v, event_ts_ms=as_of - (40 - i) * 300_000, received_ts_ms=as_of - (40 - i) * 300_000) for i, v in enumerate(ls_hist_vals)]
    ls_hist.append(TimestampedValue(value=2.0, event_ts_ms=as_of - 1000, received_ts_ms=as_of - 1000))  # long-skewed
    return DerivativesState(
        symbol="BTCUSDT", funding_rate_history=funding_hist, open_interest_history_5m=oi_5m,
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
        long_short_account_ratio_history=ls_hist, taker_long_short_ratio_history=[], premium_index_current=None,
    )


def _derivatives_shorts_crowded(as_of):
    n_funding = 100
    funding_vals = [-0.0001] * (n_funding - 1) + [-0.006]
    funding_hist = [TimestampedValue(value=v, event_ts_ms=as_of - (n_funding - i) * 1000, received_ts_ms=as_of - (n_funding - i) * 1000) for i, v in enumerate(funding_vals)]
    oi_1d = [TimestampedValue(value=1_000_000 + i * 1000, event_ts_ms=as_of - (30 - i) * 86_400_000, received_ts_ms=as_of) for i in range(30)]
    oi_5m = [TimestampedValue(value=1_000_000 + i * 30, event_ts_ms=as_of - 30_000 - (59 - i) * IV_5M, received_ts_ms=as_of - 30_000 - (59 - i) * IV_5M + 500) for i in range(59)]
    oi_5m.append(TimestampedValue(value=1_050_000, event_ts_ms=as_of - 30_000, received_ts_ms=as_of - 30_000 + 500))
    ls_hist_vals = [1.0 - i * 0.005 for i in range(40)]  # descending baseline
    ls_hist = [TimestampedValue(value=v, event_ts_ms=as_of - (40 - i) * 300_000, received_ts_ms=as_of - (40 - i) * 300_000) for i, v in enumerate(ls_hist_vals)]
    ls_hist.append(TimestampedValue(value=0.1, event_ts_ms=as_of - 1000, received_ts_ms=as_of - 1000))  # short-skewed
    return DerivativesState(
        symbol="BTCUSDT", funding_rate_history=funding_hist, open_interest_history_5m=oi_5m,
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=oi_1d,
        long_short_account_ratio_history=ls_hist, taker_long_short_ratio_history=[], premium_index_current=None,
    )


def _snapshot(bars_5m, bars_15m, deriv, taker_buy_ratio_pair, as_of):
    last_price = bars_15m[-1].close
    ob = OrderBookState(symbol="BTCUSDT", best_bid=last_price - 0.05, best_ask=last_price + 0.05,
                        bid_depth_5lvl_usd=2_000_000, ask_depth_5lvl_usd=2_000_000, event_ts_ms=as_of, received_ts_ms=as_of)
    buy, total = taker_buy_ratio_pair
    tf = TakerFlowState(symbol="BTCUSDT", taker_buy_base_last_bars=[buy, buy, buy], total_volume_last_bars=[total, total, total],
                        event_ts_ms=as_of, received_ts_ms=as_of)
    fh = {"btcusdt@aggTrade": FeedHealth(symbol="BTCUSDT", stream="btcusdt@aggTrade", last_message_received_ts_ms=as_of - 500, reconnect_count_window=0, is_connected=True)}
    return MarketSnapshot(
        snapshot_version="s3test", symbol="BTCUSDT", as_of_ts_ms=as_of,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars_5m),
                "15m": SymbolKlines(symbol="BTCUSDT", timeframe="15m", bars=bars_15m),
                "1h": SymbolKlines(symbol="BTCUSDT", timeframe="1h", bars=_flat(30, 3_600_000, last_price)),
                "4h": SymbolKlines(symbol="BTCUSDT", timeframe="4h", bars=_flat(30, 14_400_000, last_price))},
        orderbook=ob, taker_flow=tf, derivatives=deriv, feed_health=fh,
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def build_realistic_s3_short_reversal_snapshot():
    bars_5m = _bleed_5m_bars()
    bars_15m, final_close = _swing_low_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_longs_crowded(as_of)
    return _snapshot(bars_5m, bars_15m, deriv, (2, 10), as_of)  # buy_ratio 0.2 < 0.5 -> sell flip


def build_realistic_s3_long_reversal_snapshot():
    bars_5m = _bleed_5m_bars(start_price=P - 3000, step=-25)
    bars_15m, final_close = _swing_high_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_shorts_crowded(as_of)
    return _snapshot(bars_5m, bars_15m, deriv, (8, 10), as_of)  # buy_ratio 0.8 > 0.5 -> buy flip


def test_s3_returns_empty_without_derivatives():
    bars_5m = _bleed_5m_bars()
    bars_15m, _ = _swing_low_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    snap = _snapshot(bars_5m, bars_15m, _derivatives_longs_crowded(as_of), (2, 10), as_of)
    snap = replace(snap, derivatives=None)
    result = S3FundingCrowding().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s3_returns_empty_with_stale_funding():
    snap = build_realistic_s3_short_reversal_snapshot()
    stale_funding = [replace(tv, event_ts_ms=tv.event_ts_ms - 100_000_000, received_ts_ms=tv.received_ts_ms - 100_000_000) for tv in snap.derivatives.funding_rate_history]
    snap = replace(snap, derivatives=replace(snap.derivatives, funding_rate_history=stale_funding))
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s3_returns_empty_with_stale_oi():
    snap = build_realistic_s3_short_reversal_snapshot()
    stale_oi = [replace(tv, event_ts_ms=tv.event_ts_ms - 10_000_000, received_ts_ms=tv.received_ts_ms - 10_000_000) for tv in snap.derivatives.open_interest_history_5m]
    snap = replace(snap, derivatives=replace(snap.derivatives, open_interest_history_5m=stale_oi))
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s3_returns_empty_with_stale_ratios():
    snap = build_realistic_s3_short_reversal_snapshot()
    stale_ls = [replace(tv, event_ts_ms=tv.event_ts_ms - 1_000_000, received_ts_ms=tv.received_ts_ms - 1_000_000) for tv in snap.derivatives.long_short_account_ratio_history]
    snap = replace(snap, derivatives=replace(snap.derivatives, long_short_account_ratio_history=stale_ls))
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s3_returns_empty_without_taker_flow():
    snap = build_realistic_s3_short_reversal_snapshot()
    snap = replace(snap, taker_flow=None)
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert result == []


def test_s3_no_trigger_when_funding_z_insufficient():
    bars_5m = _bleed_5m_bars()
    bars_15m, _ = _swing_low_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_longs_crowded(as_of)
    weak_funding = [replace(tv, value=0.0001) for tv in deriv.funding_rate_history]  # no spike
    deriv = replace(deriv, funding_rate_history=weak_funding)
    snap = _snapshot(bars_5m, bars_15m, deriv, (2, 10), as_of)
    result = S3FundingCrowding().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s3_no_trigger_without_structure_break():
    """Same crowding extremes, but price never breaks the swing level
    (flat 15m bars instead)."""
    bars_5m = _bleed_5m_bars()
    bars_15m = _flat(40, IV_15M, P)
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_longs_crowded(as_of)
    snap = _snapshot(bars_5m, bars_15m, deriv, (2, 10), as_of)
    result = S3FundingCrowding().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s3_no_trigger_without_taker_flip():
    """Crowding + structure break, but taker flow does NOT flip
    (buy ratio stays > 0.5 despite longs being crowded and a down
    break -- thesis unconfirmed)."""
    bars_5m = _bleed_5m_bars()
    bars_15m, _ = _swing_low_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_longs_crowded(as_of)
    snap = _snapshot(bars_5m, bars_15m, deriv, (8, 10), as_of)  # buy ratio 0.8, no flip to selling
    result = S3FundingCrowding().evaluate(snap, _empty_news(as_of), CFG)
    assert result == []


def test_s3_triggers_short_reversal_on_realistic_longs_crowded_setup():
    snap = build_realistic_s3_short_reversal_snapshot()
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert c.direction.value == "SHORT"
    assert c.strategy_source == "S3"
    assert c.tp4 < c.tp3 < c.tp2 < c.tp1 < c.entry_low <= c.entry_high < c.stop_loss
    assert validate_meta_contract(c) == []


def test_s3_triggers_long_reversal_on_realistic_shorts_crowded_setup():
    snap = build_realistic_s3_long_reversal_snapshot()
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert c.direction.value == "LONG"
    assert c.strategy_source == "S3"
    assert c.stop_loss < c.entry_low <= c.entry_high < c.tp1 < c.tp2 < c.tp3 < c.tp4


def test_s3_candidate_clears_min_stop_cost_filter():
    snap = build_realistic_s3_short_reversal_snapshot()
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    mid = (c.entry_low + c.entry_high) / 2
    r = abs(mid - c.stop_loss)
    round_trip = 2 * mid * snap.fee_maker_bps / 1e4
    assert r / round_trip >= CFG["common"]["min_stop_cost_multiple"]


# ---------------------------------------------------------------------------
# Hard rule: never signal from funding alone
# ---------------------------------------------------------------------------


def test_s3_hard_rule_funding_extreme_alone_never_signals():
    """Funding extreme present, but OI percentile NOT extreme (and
    hence the ls_ratio/price-displacement/structure/taker chain never
    even gets a chance to independently confirm) -- must be NO TRADE.
    This directly exercises the spec's 'NEVER emit a signal from
    funding alone' rule via the actual gating logic (not just the
    internal assertion, which only runs once every OTHER condition
    already passed).

    S3 percentile-ranks the LIVE 5m OI point (open_interest_history_5m[-1])
    against the 1d history series. The inclusive percentile convention
    makes a tied maximum rank 1.0, so this fixture uses a varied history
    centered around the live point to keep the OI percentile mid-range.
    """
    bars_5m = _bleed_5m_bars()
    bars_15m, _ = _swing_low_15m_bars()
    as_of = max(bars_5m[-1].close_time_ms, bars_15m[-1].close_time_ms) + 1000
    deriv = _derivatives_longs_crowded(as_of)
    live_oi_value = deriv.open_interest_history_5m[-1].value
    centered_oi_1d = [
        replace(tv, value=live_oi_value + (i - 14) * 5_000)
        for i, tv in enumerate(deriv.open_interest_history_1d)
    ]
    deriv = replace(deriv, open_interest_history_1d=centered_oi_1d)
    snap = _snapshot(bars_5m, bars_15m, deriv, (2, 10), as_of)
    result = S3FundingCrowding().evaluate(snap, _empty_news(as_of), CFG)
    assert result == [], "funding extreme alone (without OI/ratio/displacement/structure/taker) must not signal"


def test_s3_assert_never_funding_alone_raises_on_incomplete_conjunction():
    """Direct unit test of the internal hard-rule assertion: calling it
    with any single False must raise AssertionError."""
    from app.strategies.s3_funding_crowding import S3FundingCrowding as S3

    all_true = dict(funding_extreme=True, oi_extreme=True, price_disp_extreme=True, structure_break=True, taker_flip=True)
    S3._assert_never_funding_alone(**all_true)  # must not raise

    for key in all_true:
        kwargs = dict(all_true)
        kwargs[key] = False
        with pytest.raises(AssertionError):
            S3._assert_never_funding_alone(**kwargs)


# ---------------------------------------------------------------------------
# Double-counting: funding/OI/ratios collapse to exactly two channels
# ---------------------------------------------------------------------------


def test_s3_channels_collapse_to_funding_and_oi_only():
    """S3's candidate must declare exactly {FUNDING, OI} as its
    canonical channels -- funding_z, oi_percentile, AND ls_ratio_pct
    all feed the same two channels, never three or four independent
    votes (spec section 11 double-counting avoidance)."""
    snap = build_realistic_s3_short_reversal_snapshot()
    result = S3FundingCrowding().evaluate(snap, _empty_news(snap.as_of_ts_ms), CFG)
    assert len(result) == 1
    c = result[0]
    assert set(c.channels) == {ChannelName.FUNDING, ChannelName.OI}
    assert len(c.channels) == 2


def test_s3_registry_channels_match_strategy_output():
    from app.risk.channels import channels_for_strategy

    assert set(channels_for_strategy("S3")) == {ChannelName.FUNDING, ChannelName.OI}
