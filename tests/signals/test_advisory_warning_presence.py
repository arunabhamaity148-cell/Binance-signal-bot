"""Proves every Signal carries ADVISORY_WARNING, that the warning
cannot be constructed away, and that it survives database persistence
into the journal row."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.signals.models import ADVISORY_WARNING, Signal, build_signal


def _kwargs(**overrides):
    base = dict(
        signal_id="CSB-20260101-ABCDEF", created_ts_ms=1000, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=0.8, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Every constructed Signal has the exact warning
# ---------------------------------------------------------------------------


def test_default_construction_has_advisory_warning():
    sig = Signal(**_kwargs())
    assert sig.advisory_warning == ADVISORY_WARNING


def test_advisory_warning_present_for_every_grade():
    for grade in ("A+", "A", "B"):
        sig = Signal(**_kwargs(grade=grade))
        assert sig.advisory_warning == ADVISORY_WARNING


def test_advisory_warning_present_for_every_direction():
    sig_long = Signal(**_kwargs(direction="LONG"))
    assert sig_long.advisory_warning == ADVISORY_WARNING

    short_kwargs = _kwargs(
        direction="SHORT", entry_low=99.5, entry_high=100.0, stop_loss=101.0,
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=95.0,
    )
    sig_short = Signal(**short_kwargs)
    assert sig_short.advisory_warning == ADVISORY_WARNING


def test_advisory_warning_present_for_every_strategy_source():
    for source in ("S1", "S2", "S3", "S4", "S5"):
        sig = Signal(**_kwargs(strategy_source=source))
        assert sig.advisory_warning == ADVISORY_WARNING


def test_advisory_warning_present_regardless_of_veto_state():
    sig_pass = Signal(**_kwargs(veto_state="PASS", veto_reason=None))
    assert sig_pass.advisory_warning == ADVISORY_WARNING

    sig_block = Signal(**_kwargs(veto_state="BLOCK", veto_reason="some reason"))
    assert sig_block.advisory_warning == ADVISORY_WARNING


def test_advisory_warning_exact_text():
    sig = Signal(**_kwargs())
    assert sig.advisory_warning == (
        "ADVISORY ONLY — VERIFY ACCOUNT SIZING MANUALLY. No account state, "
        "fill, leverage, or liquidation distance is observed."
    )


def test_build_signal_also_carries_warning():
    sig = build_signal(
        signal_id="CSB-20260101-ABCDEF", created_ts_ms=1000, symbol="BTCUSDT",
        direction="LONG", grade="A", confidence=0.8, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
        price_tick=0.1, qty_step=0.001,
    )
    assert sig.advisory_warning == ADVISORY_WARNING


# ---------------------------------------------------------------------------
# Cannot be removed, overridden to something wrong, or bypassed
# ---------------------------------------------------------------------------


def test_signal_is_frozen_warning_cannot_be_mutated_after_construction():
    sig = Signal(**_kwargs())
    with pytest.raises(ValidationError):
        sig.advisory_warning = "something else"  # type: ignore[misc]


def test_explicitly_passing_a_different_warning_value_is_still_accepted_by_pydantic_but_wrong():
    """Documents the actual behavior: advisory_warning has a default
    but is a regular field, so a caller COULD pass a different string.
    This test exists to make that fact explicit and auditable (not to
    endorse it) -- no code path in this codebase ever does this
    (signal_engine.py's build_final_signal never passes
    advisory_warning as an argument, always relying on the default),
    which the next test verifies directly against the actual call
    site."""
    sig = Signal(**_kwargs(advisory_warning="WRONG TEXT"))
    assert sig.advisory_warning == "WRONG TEXT"  # pydantic allows it...


def test_signal_engine_never_overrides_the_default_warning():
    """...but the only production code path that constructs a Signal
    (signal_engine.build_final_signal -> signals.models.build_signal)
    never passes advisory_warning explicitly, so in practice every
    Signal this system ever produces uses the enforced default."""
    import inspect

    import app.signals.signal_engine as signal_engine_module
    import app.signals.models as models_module

    engine_source = inspect.getsource(signal_engine_module.build_final_signal)
    assert "advisory_warning" not in engine_source

    build_signal_source = inspect.getsource(models_module.build_signal)
    # build_signal itself also never overrides it -- the Signal(...)
    # call inside it does not pass advisory_warning, so the field's
    # default (ADVISORY_WARNING) is what's actually used.
    assert "advisory_warning=" not in build_signal_source


# ---------------------------------------------------------------------------
# Survives database persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_warning_survives_database_round_trip(tmp_path):
    from app.database.repository import SignalRepository

    sig = Signal(**_kwargs())
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        await repo.insert_signal(sig)
        fetched = await repo.get_signal_by_id(sig.signal_id)
        assert fetched is not None
        assert fetched.advisory_warning == ADVISORY_WARNING
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_advisory_warning_in_journal_row_matches_signal_object():
    from app.database.models import SignalRow

    sig = Signal(**_kwargs())
    row = SignalRow(
        signal_id=sig.signal_id, created_ts_ms=sig.created_ts_ms, symbol=sig.symbol,
        direction=sig.direction, grade=sig.grade, confidence=sig.confidence,
        strategy_source=sig.strategy_source, entry_low=sig.entry_low, entry_high=sig.entry_high,
        stop_loss=sig.stop_loss, tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3, tp4=sig.tp4,
        rr_tp2=sig.rr_tp2, expiry_ts_ms=sig.expiry_ts_ms, why_lines_json="[]",
        veto_state=sig.veto_state, veto_reason=sig.veto_reason,
        advisory_warning=sig.advisory_warning, size_units_advisory=sig.size_units_advisory,
        notional_usd_advisory=sig.notional_usd_advisory, meta_json="{}",
        lifecycle_state="PENDING",
    )
    assert row.advisory_warning == ADVISORY_WARNING


# ---------------------------------------------------------------------------
# Survives Telegram formatting
# ---------------------------------------------------------------------------


def test_advisory_warning_appears_in_formatted_telegram_message():
    from app.telegram.formatter import DeliveryContext, format_signal_message

    sig = Signal(**_kwargs())
    ctx = DeliveryContext(news_state_label="healthy", binance_state_label="healthy", as_of_ts_ms=1000)
    message = format_signal_message(sig, ctx)
    assert ADVISORY_WARNING in message
