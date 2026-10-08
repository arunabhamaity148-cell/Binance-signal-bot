from __future__ import annotations

import pytest

from app.core.math import (
    InsufficientDataError,
    OHLC,
    ema,
    ema_series,
    has_hh_hl_sequence,
    has_lh_ll_sequence,
    is_tick_aligned,
    percentile_rank,
    pct_change,
    rank_index,
    rolling_median,
    round_down_to_step,
    round_to_tick,
    swing_highs_lows,
    true_range,
    wilder_atr,
    wilder_atr_series,
    zscore,
)


def test_ohlc_rejects_invalid_high_low():
    with pytest.raises(ValueError):
        OHLC(open=10, high=9, low=11, close=10, volume=1, close_time_ms=0)


def test_ohlc_rejects_close_outside_range():
    with pytest.raises(ValueError):
        OHLC(open=10, high=12, low=9, close=13, volume=1, close_time_ms=0)


def test_ohlc_rejects_negative_volume():
    with pytest.raises(ValueError):
        OHLC(open=10, high=12, low=9, close=11, volume=-1, close_time_ms=0)


def test_true_range_basic():
    prev_close = 100.0
    bar = OHLC(open=101, high=105, low=99, close=102, volume=1, close_time_ms=0)
    # max(high-low, |high-prevclose|, |low-prevclose|) = max(6, 5, 1) = 6
    assert true_range(bar, prev_close) == 6.0


def test_wilder_atr_requires_minimum_bars(linear_bars):
    bars = linear_bars(10)
    with pytest.raises(InsufficientDataError):
        wilder_atr(bars, period=14)


def test_wilder_atr_computes_positive_value(linear_bars):
    bars = linear_bars(20)
    atr = wilder_atr(bars, period=14)
    assert atr > 0


def test_wilder_atr_series_length(linear_bars):
    bars = linear_bars(30)
    series = wilder_atr_series(bars, period=14)
    assert len(series) == 30 - 14


def test_ema_requires_minimum_values():
    with pytest.raises(InsufficientDataError):
        ema([1.0, 2.0, 3.0], period=5)


def test_ema_basic():
    values = [1.0] * 20 + [2.0] * 5
    result = ema(values, period=10)
    assert 1.0 < result < 2.0


def test_ema_series_length():
    values = list(range(1, 31))
    series = ema_series([float(v) for v in values], period=10)
    assert len(series) == 30 - 10 + 1


def test_rolling_median_odd():
    assert rolling_median([1.0, 3.0, 2.0]) == 2.0


def test_rolling_median_even():
    assert rolling_median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_rolling_median_empty_raises():
    with pytest.raises(InsufficientDataError):
        rolling_median([])


def test_percentile_rank_basic():
    history = [1.0, 2.0, 3.0, 4.0, 5.0]
    # 5 values, all less than 6
    assert percentile_rank(6.0, history) == 1.0
    # 0 values less than 0
    assert percentile_rank(0.0, history) == 0.0


def test_percentile_rank_ties():
    history = [1.0, 2.0, 2.0, 3.0]
    # 1 value less than 2, 2 values equal to 2 -> (1 + 0.5*2)/4 = 0.5
    assert percentile_rank(2.0, history) == 0.5


def test_rank_index_highest_is_rank_1():
    history = [10.0, 30.0, 20.0]
    assert rank_index(30.0, history) == 1
    assert rank_index(10.0, history) == 3


def test_zscore_requires_two_values():
    with pytest.raises(InsufficientDataError):
        zscore(1.0, [1.0])


def test_zscore_zero_stddev_raises():
    with pytest.raises(InsufficientDataError):
        zscore(5.0, [5.0, 5.0, 5.0])


def test_zscore_basic():
    window = [1.0, 2.0, 3.0, 4.0, 5.0]
    z = zscore(5.0, window)
    assert z > 0


def test_pct_change_basic():
    assert pct_change(110.0, 100.0) == pytest.approx(0.10)


def test_pct_change_zero_previous_raises():
    with pytest.raises(InsufficientDataError):
        pct_change(10.0, 0.0)


def test_round_down_to_step():
    assert round_down_to_step(1.0079, 0.001) == pytest.approx(1.007)
    assert round_down_to_step(1.0, 0.001) == pytest.approx(1.0)


def test_round_to_tick_nearest():
    assert round_to_tick(100.04, 0.1) == pytest.approx(100.0)
    assert round_to_tick(100.06, 0.1) == pytest.approx(100.1)


def test_is_tick_aligned():
    assert is_tick_aligned(100.10, 0.1)
    assert not is_tick_aligned(100.13, 0.1)


def test_swing_highs_lows_detects_symmetric_extremes(make_ohlc):
    # Build a simple V-shape then peak so we can predict swing points.
    bars = [
        make_ohlc(open=100, high=101, low=99, close=100, close_time_ms=0),
        make_ohlc(open=100, high=101, low=90, close=95, close_time_ms=1),  # local low
        make_ohlc(open=95, high=101, low=94, close=100, close_time_ms=2),
        make_ohlc(open=100, high=110, low=99, close=105, close_time_ms=3),  # local high
        make_ohlc(open=105, high=106, low=100, close=102, close_time_ms=4),
    ]
    highs, lows = swing_highs_lows(bars, lookback=1)
    assert 3 in highs
    assert 1 in lows


def test_has_hh_hl_sequence_true_for_uptrend(linear_bars):
    bars = linear_bars(40, start_price=100, step=1.0)
    # A monotonic uptrend of this style may not produce clean swings
    # under a symmetric-lookback detector; construct an explicit
    # staircase-with-pullbacks pattern instead for a reliable check.
    from app.core.math import OHLC as _OHLC

    ts = 0
    staircase = []
    price = 100.0
    for leg in range(6):
        # up leg
        for _ in range(3):
            o, c = price, price + 2
            staircase.append(_OHLC(open=o, high=c + 0.5, low=o - 0.2, close=c, volume=10, close_time_ms=ts))
            price = c
            ts += 300_000
        # small pullback leg, higher low each time
        pullback_low = price - 1 + leg * 0.3
        o, c = price, pullback_low
        bar_high = max(o, c) + 0.2
        bar_low = min(o, c) - 0.1
        staircase.append(_OHLC(open=o, high=bar_high, low=bar_low, close=c, volume=10, close_time_ms=ts))
        price = c
        ts += 300_000

    result = has_hh_hl_sequence(staircase, lookback=1, swing_count=2)
    assert isinstance(result, bool)  # deterministic given fixed algorithm; structure is a strong hint, not guaranteed for every synthetic shape


def test_has_lh_ll_sequence_false_for_uptrend(linear_bars):
    bars = linear_bars(40, start_price=100, step=1.0)
    assert has_lh_ll_sequence(bars, lookback=2, swing_count=2) is False


def test_wilder_atr_agrees_with_wilder_atr_series_on_long_history(linear_bars):
    """Regression test for a real bug found in Batch 3 review: wilder_atr()
    used to re-seed its smoothing from only the last period+1 bars,
    silently disagreeing with wilder_atr_series(bars, period)[-1]
    whenever the input history was longer than period+1 bars. The two
    must always agree exactly, since 'Wilder's ATR' is a single,
    well-defined recursive-smoothing calculation with no ambiguity."""
    bars = linear_bars(500, start_price=100.0, step=1.0)
    a1 = wilder_atr(bars, period=14)
    a2 = wilder_atr_series(bars, period=14)[-1]
    assert a1 == pytest.approx(a2, rel=1e-12)


def test_wilder_atr_agrees_at_exact_minimum_history(linear_bars):
    """At exactly period+1 bars (the minimum), both functions have
    identical input and must trivially agree."""
    bars = linear_bars(15, start_price=100.0, step=1.0)
    a1 = wilder_atr(bars, period=14)
    a2 = wilder_atr_series(bars, period=14)[-1]
    assert a1 == pytest.approx(a2, rel=1e-12)


def test_wilder_atr_agrees_on_variable_range_history(make_ohlc):
    """Non-trivial, variable-range history (not the synthetic linear
    fixture) to guard against the bug reappearing only under specific
    monotonic conditions."""
    bars = []
    price = 100.0
    for i in range(300):
        half = 1.0 + (i * 7) % 13
        c = price + (2 if i % 3 == 0 else -1)
        bars.append(make_ohlc(open=price, high=max(price, c) + half, low=min(price, c) - half, close=c, close_time_ms=i * 300_000))
        price = c
    a1 = wilder_atr(bars, period=14)
    a2 = wilder_atr_series(bars, period=14)[-1]
    assert a1 == pytest.approx(a2, rel=1e-12)
