from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.signals.models import ADVISORY_WARNING, Signal, build_signal


def _valid_long_kwargs(**overrides):
    base = dict(
        signal_id="CSB-20260101-ABCDEF",
        created_ts_ms=1000,
        symbol="BTCUSDT",
        direction="LONG",
        grade="A",
        confidence=0.8,
        strategy_source="S1",
        entry_low=100.0,
        entry_high=100.5,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        tp3=103.0,
        tp4=105.0,
        rr_tp2=1.5,
        expiry_ts_ms=2000,
        why_lines=["reason 1"],
        veto_state="PASS",
        veto_reason=None,
        size_units_advisory=1.0,
        notional_usd_advisory=100.0,
        meta={},
    )
    base.update(overrides)
    return base


def test_valid_long_signal_constructs():
    sig = Signal(**_valid_long_kwargs())
    assert sig.direction == "LONG"
    assert sig.advisory_warning == ADVISORY_WARNING


def test_valid_short_signal_constructs():
    kwargs = _valid_long_kwargs(
        direction="SHORT", entry_low=99.5, entry_high=100.0, stop_loss=101.0,
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=95.0,
    )
    sig = Signal(**kwargs)
    assert sig.direction == "SHORT"


def test_long_signal_wrong_ordering_raises():
    kwargs = _valid_long_kwargs(stop_loss=100.2)  # stop_loss >= entry_low
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_long_signal_tp_not_ascending_raises():
    kwargs = _valid_long_kwargs(tp2=100.9)  # tp2 < tp1
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_short_signal_wrong_ordering_raises():
    kwargs = _valid_long_kwargs(
        direction="SHORT", entry_low=99.5, entry_high=100.0, stop_loss=99.0,  # stop_loss should be > entry_high
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=95.0,
    )
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_entry_low_greater_than_entry_high_raises():
    kwargs = _valid_long_kwargs(entry_low=101.0, entry_high=100.0)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_negative_price_raises():
    kwargs = _valid_long_kwargs(stop_loss=-1.0)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_zero_price_raises():
    kwargs = _valid_long_kwargs(tp1=0.0)
    # tp1=0 also breaks ordering, but positivity check should catch it too
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_confidence_out_of_range_raises():
    kwargs = _valid_long_kwargs(confidence=1.5)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_confidence_negative_raises():
    kwargs = _valid_long_kwargs(confidence=-0.1)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_too_many_why_lines_raises():
    kwargs = _valid_long_kwargs(why_lines=[f"line {i}" for i in range(11)])
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_exactly_ten_why_lines_ok():
    kwargs = _valid_long_kwargs(why_lines=[f"line {i}" for i in range(10)])
    sig = Signal(**kwargs)
    assert len(sig.why_lines) == 10


def test_block_without_reason_raises():
    kwargs = _valid_long_kwargs(veto_state="BLOCK", veto_reason=None)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_pass_with_reason_raises():
    kwargs = _valid_long_kwargs(veto_state="PASS", veto_reason="should not be set")
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_block_with_reason_ok():
    kwargs = _valid_long_kwargs(veto_state="BLOCK", veto_reason="G5 blocked")
    sig = Signal(**kwargs)
    assert sig.veto_state == "BLOCK"


def test_negative_size_raises():
    kwargs = _valid_long_kwargs(size_units_advisory=-1.0)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_negative_notional_raises():
    kwargs = _valid_long_kwargs(notional_usd_advisory=-1.0)
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_signal_is_frozen():
    sig = Signal(**_valid_long_kwargs())
    with pytest.raises(ValidationError):
        sig.confidence = 0.99  # type: ignore[misc]


def test_invalid_direction_literal_raises():
    kwargs = _valid_long_kwargs(direction="SIDEWAYS")
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_invalid_grade_literal_raises():
    kwargs = _valid_long_kwargs(grade="C")
    with pytest.raises(ValidationError):
        Signal(**kwargs)


def test_invalid_strategy_source_raises():
    kwargs = _valid_long_kwargs(strategy_source="S9")
    with pytest.raises(ValidationError):
        Signal(**kwargs)


# ---------------------------------------------------------------------------
# build_signal (tick/qty-step alignment)
# ---------------------------------------------------------------------------


def test_build_signal_aligned_prices_succeed():
    sig = build_signal(
        signal_id="CSB-20260101-ABCDEF", created_ts_ms=1000, symbol="BTCUSDT",
        direction="LONG", grade="A", confidence=0.8, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0079, notional_usd_advisory=100.0, meta={},
        price_tick=0.1, qty_step=0.001,
    )
    assert sig.size_units_advisory == pytest.approx(1.007)


def test_build_signal_misaligned_price_raises():
    with pytest.raises(ValueError):
        build_signal(
            signal_id="CSB-20260101-ABCDEF", created_ts_ms=1000, symbol="BTCUSDT",
            direction="LONG", grade="A", confidence=0.8, strategy_source="S1",
            entry_low=100.03, entry_high=100.5, stop_loss=99.0,
            tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
            expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
            size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
            price_tick=0.1, qty_step=0.001,
        )


def test_build_signal_rounds_size_down():
    sig = build_signal(
        signal_id="CSB-20260101-ABCDEF", created_ts_ms=1000, symbol="BTCUSDT",
        direction="LONG", grade="A", confidence=0.8, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.9999, notional_usd_advisory=100.0, meta={},
        price_tick=0.1, qty_step=1.0,
    )
    assert sig.size_units_advisory == 1.0
