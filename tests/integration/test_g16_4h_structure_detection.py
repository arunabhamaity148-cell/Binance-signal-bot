from __future__ import annotations

from app.core.math import OHLC, has_hh_hl_sequence, has_lh_ll_sequence


def _structure_bars(direction: str) -> list[OHLC]:
    bars = []
    for i in range(40):
        base = 100.0 + i if direction == "up" else 100.0 - i
        high = base + 1.0
        low = base - 1.0
        if direction == "up" and i in (6, 16, 26):
            high = base + (6.0 + (i - 6) / 5.0)
        if direction == "up" and i in (10, 20, 30):
            low = base - (10.0 - (i - 10) / 5.0)
        if direction == "down" and i in (6, 16, 26):
            high = base + (10.0 - (i - 6) / 5.0)
        if direction == "down" and i in (10, 20, 30):
            low = base - (10.0 - (i - 10) / 5.0)
        bars.append(OHLC(base, high, low, base, 100.0, i))
    return bars


def test_clear_lower_high_lower_low_sequence_is_detected():
    assert has_lh_ll_sequence(_structure_bars("down"), lookback=2, swing_count=3)


def test_clear_higher_high_higher_low_sequence_is_detected():
    assert has_hh_hl_sequence(_structure_bars("up"), lookback=2, swing_count=3)
