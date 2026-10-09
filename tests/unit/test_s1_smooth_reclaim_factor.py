from __future__ import annotations

import pytest

from app.strategies.s1_liquidity_sweep import threshold_factor


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        (0.0, 0.0),
        (0.05, 1 / 6),
        (0.10, 1 / 3),
        (0.15, 0.5),
        (0.30, 1.0),
        (-0.05, 0.0),
    ],
)
def test_reclaim_distance_uses_smooth_ramp(distance, expected):
    assert threshold_factor(distance, 0.15) == pytest.approx(expected)
