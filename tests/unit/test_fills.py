from __future__ import annotations

import pytest

from app.backtest.fills import (
    PartialExitEvent,
    PositionState,
    assess_limit_fill,
    process_bar_for_open_position,
)
from app.core.math import OHLC
from app.core.models import Direction


def _bar(o, h, l, c, ts=0, v=10.0):
    return OHLC(open=o, high=h, low=l, close=c, volume=v, close_time_ms=ts)


# ---------------------------------------------------------------------------
# assess_limit_fill
# ---------------------------------------------------------------------------


def test_no_overlap_not_filled():
    bar = _bar(100, 101, 99, 100.5)
    r = assess_limit_fill(entry_low=110, entry_high=112, bar=bar, direction=Direction.LONG)
    assert r.is_filled is False
    assert r.fill_probability == 0.0
    assert r.fill_price is None


def test_full_overlap_probability_one():
    bar = _bar(100, 115, 95, 105)  # bar range [95,115] fully covers entry zone
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.LONG)
    assert r.is_filled is True
    assert r.fill_probability == 1.0


def test_partial_overlap_probability_proportional():
    # entry zone [99,101] width=2; bar range [100,105] overlaps [100,101] width=1 -> 0.5
    bar = _bar(102, 105, 100, 104)
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.LONG)
    assert r.is_filled is True
    assert r.fill_probability == pytest.approx(0.5)


def test_exact_boundary_touch_is_filled():
    bar = _bar(102, 103, 101, 102.5)  # bar low == entry_high exactly
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.LONG)
    assert r.is_filled is True


def test_fill_price_long_is_conservative_boundary():
    bar = _bar(100, 105, 99.5, 104)
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.LONG)
    # overlap_low = max(99, 99.5) = 99.5 -> LONG fill price = max(entry_low, bar.low) = 99.5
    assert r.fill_price == pytest.approx(99.5)


def test_fill_price_short_is_conservative_boundary():
    bar = _bar(100, 101.5, 99, 100.5)
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.SHORT)
    # overlap_high = min(101, 101.5) = 101 -> SHORT fill price = min(entry_high, bar.high) = 101
    assert r.fill_price == pytest.approx(101.0)


def test_zero_width_entry_zone_filled_when_touched():
    bar = _bar(100, 101, 99.5, 100.5)
    r = assess_limit_fill(entry_low=100.0, entry_high=100.0, bar=bar, direction=Direction.LONG)
    assert r.is_filled is True
    assert r.fill_probability == 1.0
    assert r.fill_price == 100.0


def test_zero_width_entry_zone_not_filled_when_missed():
    bar = _bar(100, 101, 99.5, 100.5)
    r = assess_limit_fill(entry_low=105.0, entry_high=105.0, bar=bar, direction=Direction.LONG)
    assert r.is_filled is False


def test_entry_low_greater_than_high_raises():
    bar = _bar(100, 101, 99, 100.5)
    with pytest.raises(ValueError):
        assess_limit_fill(entry_low=101, entry_high=99, bar=bar, direction=Direction.LONG)


def test_fill_probability_never_exceeds_one():
    bar = _bar(100, 200, 0, 150)  # enormous bar, entry zone tiny subset
    r = assess_limit_fill(entry_low=99, entry_high=101, bar=bar, direction=Direction.LONG)
    assert r.fill_probability <= 1.0


# ---------------------------------------------------------------------------
# PositionState / process_bar_for_open_position
# ---------------------------------------------------------------------------


def _position(direction=Direction.LONG, entry=100, stop=95, tps=(105, 110, 115, 120)):
    return PositionState(
        direction=direction, entry_price=entry, stop_loss=stop,
        tp_levels=tps, partial_exit_fractions=(0.4, 0.3, 0.2, 0.1),
    )


def test_partial_exit_fractions_must_sum_to_one():
    with pytest.raises(ValueError):
        PositionState(
            direction=Direction.LONG, entry_price=100, stop_loss=95,
            tp_levels=(105, 110, 115, 120), partial_exit_fractions=(0.5, 0.5, 0.5, 0.5),
        )


def test_tp1_hit_partial_exit():
    pos = _position()
    bar = _bar(100, 106, 99, 105.5, ts=1)
    pos = process_bar_for_open_position(pos, bar)
    assert len(pos.exits) == 1
    assert pos.exits[0].tp_index == 0
    assert pos.exits[0].fraction_of_original == 0.4
    assert pos.remaining_fraction == pytest.approx(0.6)
    assert pos.next_tp_index == 1
    assert not pos.is_fully_closed


def test_sequential_tp_hits_in_separate_bars():
    pos = _position()
    pos = process_bar_for_open_position(pos, _bar(100, 106, 99, 105.5, ts=1))
    pos = process_bar_for_open_position(pos, _bar(105, 111, 104, 110.5, ts=2))
    assert len(pos.exits) == 2
    assert pos.remaining_fraction == pytest.approx(0.3)


def test_all_four_tps_hit_fully_closes():
    pos = _position()
    pos = process_bar_for_open_position(pos, _bar(100, 106, 99, 105.5, ts=1))
    pos = process_bar_for_open_position(pos, _bar(105, 111, 104, 110.5, ts=2))
    pos = process_bar_for_open_position(pos, _bar(110, 116, 109, 115.5, ts=3))
    pos = process_bar_for_open_position(pos, _bar(115, 121, 114, 120.5, ts=4))
    assert pos.remaining_fraction == pytest.approx(0.0, abs=1e-9)
    assert pos.is_fully_closed


def test_stop_loss_hit_closes_entire_remaining_position():
    pos = _position()
    pos = process_bar_for_open_position(pos, _bar(100, 106, 99, 105.5, ts=1))  # TP1 hit, 0.6 remains
    pos = process_bar_for_open_position(pos, _bar(100, 101, 94, 95, ts=2))  # stop hit
    assert pos.stopped_out is True
    assert pos.is_fully_closed
    assert pos.stop_out_bar_close_time_ms == 2


def test_stop_and_tp_same_bar_stop_takes_precedence():
    """Conservative worst-case ordering: if a single wide bar's range
    covers both the next TP and the stop, the stop wins."""
    pos = _position()
    wide_bar = _bar(100, 106, 94, 95, ts=1)  # touches TP1 (106>=105) AND stop (94<=95... low<=95)
    pos = process_bar_for_open_position(pos, wide_bar)
    assert pos.stopped_out is True
    assert pos.exits == []  # TP must NOT have been recorded


def test_closed_position_ignores_further_bars():
    pos = _position()
    pos.stopped_out = True
    bar = _bar(100, 200, 50, 150, ts=99)
    result = process_bar_for_open_position(pos, bar)
    assert result is pos  # unchanged, no further processing
    assert result.exits == []


def test_short_direction_tp_and_sl():
    pos = PositionState(
        direction=Direction.SHORT, entry_price=100, stop_loss=105,
        tp_levels=(95, 90, 85, 80), partial_exit_fractions=(0.4, 0.3, 0.2, 0.1),
    )
    bar = _bar(100, 101, 94, 94.5, ts=1)  # low touches TP1 (95), but SHORT TP hit = low<=95
    pos = process_bar_for_open_position(pos, bar)
    assert len(pos.exits) == 1
    assert pos.exits[0].price == 95


def test_short_stop_loss_hit():
    pos = PositionState(
        direction=Direction.SHORT, entry_price=100, stop_loss=105,
        tp_levels=(95, 90, 85, 80), partial_exit_fractions=(0.4, 0.3, 0.2, 0.1),
    )
    bar = _bar(100, 106, 99, 105.5, ts=1)  # high >= 105 -> stop hit for SHORT
    pos = process_bar_for_open_position(pos, bar)
    assert pos.stopped_out is True
