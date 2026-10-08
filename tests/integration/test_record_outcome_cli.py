from __future__ import annotations

import asyncio

from app.database.repository import SignalRepository
from app.signals.models import Signal
from scripts.record_outcome import main


def _signal() -> Signal:
    return Signal(
        signal_id="CSB-20261008-CCCDDD", created_ts_ms=1, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=0.8, strategy_source="S1", entry_low=100.0, entry_high=101.0,
        stop_loss=99.0, tp1=102.0, tp2=103.0, tp3=104.0, tp4=105.0, rr_tp2=1.8,
        expiry_ts_ms=1000, why_lines=["fixture"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
    )


def test_record_outcome_cli_writes_operator_manual_r(tmp_path, capsys):
    db = tmp_path / "outcomes.db"
    repo = SignalRepository(db)
    async def seed():
        await repo.connect()
        await repo.insert_signal(_signal())
        await repo.close()
    asyncio.run(seed())

    assert main(["--signal-id", _signal().signal_id, "--realized-r", "1.25",
                 "--note", "manually reconciled", "--db", str(db)]) == 0
    output = capsys.readouterr()
    assert "MANUAL OUTCOME RECORDED" in output.out
    assert "+1.25R" in output.out
    assert "no exchange fill was observed" in output.out
    assert output.err == ""

    verify = SignalRepository(db)
    async def read_outcome():
        await verify.connect()
        try:
            data = await verify.get_daily_report_data(start_ts_ms=0, end_ts_ms=10**15)
            assert data["cumulative_outcomes"]["count"] == 1
            assert data["cumulative_outcomes"]["realized_r"] == 1.25
        finally:
            await verify.close()
    asyncio.run(read_outcome())


def test_record_outcome_cli_rejects_unknown_signal_and_nonfinite_r(tmp_path, capsys):
    db = tmp_path / "unknown.db"
    assert main(["--signal-id", "missing", "--realized-r", "0.5", "--db", str(db)]) == 1
    assert "not recorded" in capsys.readouterr().err
    assert main(["--signal-id", "missing", "--realized-r", "nan", "--db", str(db)]) == 2
    assert "must be finite" in capsys.readouterr().err
