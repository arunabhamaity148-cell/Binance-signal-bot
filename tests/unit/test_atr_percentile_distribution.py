from __future__ import annotations

import pytest

from app.core.math import percentile_rank


def test_increasing_200_value_distribution_has_expected_ranks():
    history = [float(value) for value in range(1, 201)]

    assert percentile_rank(100.0, history) == pytest.approx(0.5)
    assert percentile_rank(200.0, history) == pytest.approx(1.0)


def test_tied_minimum_values_receive_positive_inclusive_rank():
    history = [1.0] * 200

    assert percentile_rank(1.0, history) == pytest.approx(1.0)
    assert percentile_rank(1.0, [1.0, 1.0, 2.0]) > 0.0
