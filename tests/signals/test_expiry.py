from __future__ import annotations

import pytest

from app.signals.expiry import compute_expiry_ts_ms, is_expired


EXPIRY_CFG = {"A+": 60, "A": 45, "B": 30}


def test_compute_expiry_ts_ms_a_plus():
    result = compute_expiry_ts_ms(created_ts_ms=1_000_000, grade="A+", expiry_per_grade=EXPIRY_CFG)
    assert result == 1_000_000 + 60 * 60_000


def test_compute_expiry_ts_ms_b():
    result = compute_expiry_ts_ms(created_ts_ms=0, grade="B", expiry_per_grade=EXPIRY_CFG)
    assert result == 30 * 60_000


def test_compute_expiry_ts_ms_unknown_grade_raises():
    with pytest.raises(ValueError):
        compute_expiry_ts_ms(created_ts_ms=0, grade="C", expiry_per_grade=EXPIRY_CFG)


def test_is_expired_true_at_exact_boundary():
    assert is_expired(as_of_ts_ms=1000, expiry_ts_ms=1000) is True


def test_is_expired_true_after_boundary():
    assert is_expired(as_of_ts_ms=1001, expiry_ts_ms=1000) is True


def test_is_expired_false_before_boundary():
    assert is_expired(as_of_ts_ms=999, expiry_ts_ms=1000) is False
