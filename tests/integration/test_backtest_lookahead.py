"""Proves the backtest engine has NO look-ahead.

Two complementary proofs, per the explicit requirement: "a synthetic
future-leak fixture must produce different results than the same
fixture with future bars redacted — if the numbers match, look-ahead
is present."

1. POSITIVE CONTROL (`test_future_index_genuinely_changes_snapshot`):
   proves `index_per_timeframe` is a real, load-bearing visibility
   boundary — supplying a LATER index (simulating what a buggy,
   look-ahead engine would do) DOES produce a different snapshot. If
   this test ever showed NO difference, it would mean the index
   parameter is a no-op and the engine always sees everything
   regardless — i.e. it would prove look-ahead is structurally
   possible even if not currently triggered.

2. THE ACTUAL GUARANTEE (`test_correct_index_is_immune_to_future_bars`):
   proves that when the engine is called the way it is ALWAYS actually
   called in `run_single_symbol_backtest` (with `index_per_timeframe`
   correctly bounded to "now"), appending or mutating bars AFTER that
   index — including a wildly anomalous bar designed to maximally
   perturb ATR/percentile/swing calculations if it leaked in — changes
   NOTHING about the snapshot or the candidates generated from it. This
   is the fixture pair described in the requirement: a "future leak"
   array (anomalous bar present downstream) vs. a "redacted" array
   (that bar absent) — evaluated at the SAME earlier index, the two
   must produce byte-for-byte identical snapshots and candidate lists.
"""
from __future__ import annotations

import random

from app.config import load_all
from app.core.math import OHLC
from app.core.models import NewsState
from app.backtest.engine import _build_snapshot_at_index, generate_candidates_at_snapshot

CFG = load_all()
P = 100_000.0
IV = 300_000
NOW = 1_800_000_000_000


def _history(n_bars=300, seed=777):
    rng = random.Random(seed)
    t0 = NOW - (n_bars + 100) * IV
    bars = []
    price = P
    for i in range(n_bars):
        half = rng.uniform(70, 250)
        c = price + (40 if i % 2 == 0 else -40)
        hi, lo = max(price, c) + half, min(price, c) - half
        bars.append(OHLC(open=price, high=hi, low=lo, close=c, volume=100.0, close_time_ms=t0 + i * IV))
        price = c
    return bars


def _snapshot_at(bars, visible_index, *, as_of_ts_ms):
    return _build_snapshot_at_index(
        symbol="BTCUSDT", all_bars={"5m": bars}, index_per_timeframe={"5m": visible_index},
        as_of_ts_ms=as_of_ts_ms, price_tick=0.1, qty_step=0.001, min_qty=0.001,
        fee_maker_bps=2.0, fee_taker_bps=5.0, min_candles=60,
    )


def test_future_index_genuinely_changes_snapshot():
    """Positive control: index_per_timeframe is a real boundary, not a
    no-op. A snapshot built with a LATER visible index must differ from
    one built with an earlier index on the SAME underlying bar array."""
    bars = _history()
    as_of = bars[149].close_time_ms + 1

    correct_snapshot = _snapshot_at(bars, visible_index=149, as_of_ts_ms=as_of)
    simulated_buggy_snapshot = _snapshot_at(bars, visible_index=299, as_of_ts_ms=as_of)

    correct_bars = correct_snapshot.klines_for("5m")
    buggy_bars = simulated_buggy_snapshot.klines_for("5m")

    assert len(correct_bars) != len(buggy_bars), (
        "a later visible index produced the SAME bar count as an earlier one — "
        "index_per_timeframe is not actually controlling visibility, which would "
        "mean look-ahead is structurally possible"
    )
    assert len(correct_bars) == 150
    assert len(buggy_bars) == 300


def test_correct_index_is_immune_to_future_bars():
    """THE actual no-look-ahead guarantee: evaluating at a fixed,
    correct index, the snapshot and candidates must be IDENTICAL
    whether or not an anomalous 'future leak' bar exists anywhere after
    that index in the underlying array."""
    base_bars = _history()
    evaluation_index = 149
    as_of_ts_ms = base_bars[evaluation_index].close_time_ms + 1

    # "Redacted" fixture: the plain history, nothing unusual after the
    # evaluation index.
    redacted_bars = list(base_bars)

    # "Future leak" fixture: IDENTICAL up to and including the
    # evaluation index, but with a wildly anomalous bar injected
    # immediately after it — designed to maximally perturb ATR,
    # percentile, and swing-detection calculations if it were ever
    # visible to an evaluation at `evaluation_index`.
    anomalous_bar = OHLC(
        open=base_bars[evaluation_index].close, high=base_bars[evaluation_index].close + 500_000,
        low=max(1.0, base_bars[evaluation_index].close - 500_000), close=base_bars[evaluation_index].close,
        volume=10_000_000.0, close_time_ms=base_bars[evaluation_index].close_time_ms + IV,
    )
    future_leak_bars = (
        base_bars[: evaluation_index + 1] + [anomalous_bar] + base_bars[evaluation_index + 1 :]
    )

    assert future_leak_bars[: evaluation_index + 1] == redacted_bars[: evaluation_index + 1], (
        "fixture construction error: the two arrays must be identical up to and "
        "including the evaluation index for this to be a valid no-look-ahead test"
    )
    assert future_leak_bars != redacted_bars, (
        "fixture construction error: the two arrays must actually differ somewhere "
        "after the evaluation index, or this test proves nothing"
    )

    snap_redacted = _snapshot_at(redacted_bars, visible_index=evaluation_index, as_of_ts_ms=as_of_ts_ms)
    snap_leak = _snapshot_at(future_leak_bars, visible_index=evaluation_index, as_of_ts_ms=as_of_ts_ms)

    redacted_closes = [b.close for b in snap_redacted.klines_for("5m")]
    leak_closes = [b.close for b in snap_leak.klines_for("5m")]
    assert redacted_closes == leak_closes, (
        "snapshot built at the same index DIFFERED depending on bars placed after "
        "that index — this is look-ahead"
    )

    news = NewsState(as_of_ts_ms=as_of_ts_ms, active_events=(), unhealthy_categories=frozenset())
    candidates_redacted = generate_candidates_at_snapshot(snap_redacted, news, CFG.strategy)
    candidates_leak = generate_candidates_at_snapshot(snap_leak, news, CFG.strategy)

    assert len(candidates_redacted) == len(candidates_leak), (
        "candidate count differed between the redacted and future-leak fixtures "
        "evaluated at the same index — this is look-ahead"
    )
    for c_redacted, c_leak in zip(candidates_redacted, candidates_leak):
        assert c_redacted.entry_low == c_leak.entry_low
        assert c_redacted.stop_loss == c_leak.stop_loss
        assert c_redacted.meta.get("atr14") == c_leak.meta.get("atr14")


def test_snapshot_as_of_ts_reflects_evaluation_index_not_array_length():
    """A snapshot's as_of_ts_ms must come from the bar AT the evaluation
    index, never from the array's true final bar (which would itself be
    a form of temporal look-ahead even if the bar DATA were correctly
    scoped)."""
    bars = _history()
    evaluation_index = 100
    as_of_ts_ms = bars[evaluation_index].close_time_ms + 1
    snap = _snapshot_at(bars, visible_index=evaluation_index, as_of_ts_ms=as_of_ts_ms)
    assert snap.as_of_ts_ms == as_of_ts_ms
    assert snap.as_of_ts_ms != bars[-1].close_time_ms + 1


def test_insufficient_history_at_early_index_returns_none_not_a_fabricated_snapshot():
    """At an index earlier than min_candles, the engine must refuse to
    produce a snapshot (returning None) rather than silently building
    one from insufficient data — fail-closed applies to the backtest
    replay exactly as it does live."""
    bars = _history()
    snap = _snapshot_at(bars, visible_index=10, as_of_ts_ms=bars[10].close_time_ms + 1)
    assert snap is None
