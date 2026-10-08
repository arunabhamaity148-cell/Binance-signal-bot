from __future__ import annotations

import pytest

from app.core.errors import SnapshotIncompleteError
from app.core.models import TimestampedValue
from app.core.models import SymbolKlines
from app.data.snapshot import SnapshotInputs, build_snapshot


def _inputs(n_5m_bars: int, make_ohlc) -> SnapshotInputs:
    bars = [
        make_ohlc(close_time_ms=i * 300_000)
        for i in range(n_5m_bars)
    ]
    return SnapshotInputs(
        symbol="BTCUSDT",
        as_of_ts_ms=n_5m_bars * 300_000,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
        orderbook=None,
        taker_flow=None,
        derivatives=None,
        feed_health={},
        price_tick=0.1,
        qty_step=0.001,
        min_qty=0.001,
        fee_maker_bps=2.0,
        fee_taker_bps=5.0,
    )


def test_build_snapshot_succeeds_with_enough_bars(make_ohlc):
    inputs = _inputs(60, make_ohlc)
    snap = build_snapshot(inputs, min_candles=60)
    assert snap.symbol == "BTCUSDT"
    assert len(snap.klines_for("5m")) == 60
    assert snap.snapshot_version.startswith("BTCUSDT-")


def test_build_snapshot_fails_with_insufficient_bars(make_ohlc):
    inputs = _inputs(30, make_ohlc)
    with pytest.raises(SnapshotIncompleteError):
        build_snapshot(inputs, min_candles=60)


def test_build_snapshot_fails_with_missing_5m_entirely(make_ohlc):
    inputs = _inputs(60, make_ohlc)
    inputs.klines = {}
    with pytest.raises(SnapshotIncompleteError):
        build_snapshot(inputs, min_candles=60)


def test_build_snapshot_version_is_unique(make_ohlc):
    inputs = _inputs(60, make_ohlc)
    snap1 = build_snapshot(inputs, min_candles=60)
    snap2 = build_snapshot(inputs, min_candles=60)
    assert snap1.snapshot_version != snap2.snapshot_version


def test_snapshot_klines_for_missing_timeframe_returns_empty(make_ohlc):
    inputs = _inputs(60, make_ohlc)
    snap = build_snapshot(inputs, min_candles=60)
    assert snap.klines_for("4h") == []


def _inputs_with_derivatives(n_5m_bars, make_ohlc, *, hist_oi, live_oi_point=None, oi_stale_ms=None):
    from app.core.models import DerivativesState

    bars = [make_ohlc(close_time_ms=i * 300_000) for i in range(n_5m_bars)]
    as_of = n_5m_bars * 300_000
    derivatives = DerivativesState(
        symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=hist_oi,
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=[],
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None,
    )
    return SnapshotInputs(
        symbol="BTCUSDT", as_of_ts_ms=as_of,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
        orderbook=None, taker_flow=None, derivatives=derivatives, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
        live_oi_point=live_oi_point, oi_stale_ms=oi_stale_ms,
    )


def test_snapshot_merges_fresh_live_oi_into_5m_series(make_ohlc):
    """The assembled snapshot's OI series must contain the live-polled
    point, not just the hist-bucket history."""
    as_of = 60 * 300_000
    hist = [
        TimestampedValue(value=1000.0, event_ts_ms=(60 - 10) * 300_000, received_ts_ms=(60 - 10) * 300_000),
        TimestampedValue(value=1010.0, event_ts_ms=(60 - 5) * 300_000, received_ts_ms=(60 - 5) * 300_000),
    ]
    live = TimestampedValue(value=1025.0, event_ts_ms=as_of - 5_000, received_ts_ms=as_of - 5_000)
    inputs = _inputs_with_derivatives(60, make_ohlc, hist_oi=hist, live_oi_point=live, oi_stale_ms=90_000)
    snap = build_snapshot(inputs, min_candles=60)

    series = snap.derivatives.open_interest_history_5m
    assert series[-1] == live, "assembled snapshot must end on the live-fresh OI point"
    assert series[:-1] == hist


def test_snapshot_does_not_merge_stale_live_oi(make_ohlc):
    """Fail-closed: a live OI point older than oi_stale_ms must NOT be
    merged in. The snapshot must fall back to the hist-only series so
    downstream staleness checks see the true (stale) state honestly,
    rather than a stale point disguised as fresh."""
    as_of = 60 * 300_000
    hist = [
        TimestampedValue(value=1000.0, event_ts_ms=(60 - 10) * 300_000, received_ts_ms=(60 - 10) * 300_000),
        TimestampedValue(value=1010.0, event_ts_ms=(60 - 5) * 300_000, received_ts_ms=(60 - 5) * 300_000),
    ]
    stale_live = TimestampedValue(value=9999.0, event_ts_ms=as_of - 200_000, received_ts_ms=as_of - 200_000)
    inputs = _inputs_with_derivatives(60, make_ohlc, hist_oi=hist, live_oi_point=stale_live, oi_stale_ms=90_000)
    snap = build_snapshot(inputs, min_candles=60)

    assert snap.derivatives.open_interest_history_5m == hist
    assert 9999.0 not in [tv.value for tv in snap.derivatives.open_interest_history_5m]


def test_snapshot_no_live_point_leaves_hist_series_unchanged(make_ohlc):
    as_of = 60 * 300_000
    hist = [TimestampedValue(value=1000.0, event_ts_ms=as_of - 300_000, received_ts_ms=as_of - 300_000)]
    inputs = _inputs_with_derivatives(60, make_ohlc, hist_oi=hist, live_oi_point=None)
    snap = build_snapshot(inputs, min_candles=60)
    assert snap.derivatives.open_interest_history_5m == hist


def test_snapshot_live_point_without_stale_budget_does_not_merge(make_ohlc):
    """A live point supplied without oi_stale_ms is a caller-config
    error, not a market-data condition; assembly must fail closed by
    NOT merging rather than guessing a budget."""
    as_of = 60 * 300_000
    hist = [TimestampedValue(value=1000.0, event_ts_ms=as_of - 300_000, received_ts_ms=as_of - 300_000)]
    live = TimestampedValue(value=2000.0, event_ts_ms=as_of - 1000, received_ts_ms=as_of - 1000)
    inputs = _inputs_with_derivatives(60, make_ohlc, hist_oi=hist, live_oi_point=live, oi_stale_ms=None)
    snap = build_snapshot(inputs, min_candles=60)
    assert snap.derivatives.open_interest_history_5m == hist


def test_snapshot_no_derivatives_state_with_live_point_is_noop(make_ohlc):
    bars = [make_ohlc(close_time_ms=i * 300_000) for i in range(60)]
    inputs = SnapshotInputs(
        symbol="BTCUSDT", as_of_ts_ms=60 * 300_000,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
        live_oi_point=TimestampedValue(value=1.0, event_ts_ms=1, received_ts_ms=1), oi_stale_ms=90_000,
    )
    snap = build_snapshot(inputs, min_candles=60)
    assert snap.derivatives is None
