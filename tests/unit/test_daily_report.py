from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.database.repository import SignalRepository
from app.signals.models import Signal
from app.telegram.reports import build_daily_report, ist_day_bounds, next_daily_report_time
from app.telegram import reports as reports_module
from app.telegram.sender import SendOutcome

ASSUMPTIONS = {
    "account_capital_inr": 200000, "account_capital_usd": 2400,
    "risk_per_trade_inr": 5000, "risk_per_trade_pct": 2.5,
    "assumed_leverage": 10, "mode": "paper",
}


def _signal(signal_id: str, stamp: int, symbol: str, grade: str, source: str) -> Signal:
    return Signal(
        signal_id=signal_id, created_ts_ms=stamp, symbol=symbol, direction="LONG",
        grade=grade, confidence=0.85, strategy_source=source,
        entry_low=100.0, entry_high=101.0, stop_loss=99.0,
        tp1=102.0, tp2=103.0, tp3=104.0, tp4=105.0, rr_tp2=1.8,
        expiry_ts_ms=stamp + 3600_000, why_lines=["test"], veto_state="PASS",
        veto_reason=None, size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
    )


@pytest.mark.asyncio
async def test_report_aggregates_only_persisted_signals_events_and_manual_outcomes(tmp_path):
    repo = SignalRepository(tmp_path / "daily.db")
    await repo.connect()
    now = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)
    day, start, end = ist_day_bounds(now)
    try:
        first = _signal("CSB-20261008-AAAAAA", start + 1000, "BTCUSDT", "A", "S3")
        second = _signal("CSB-20261008-BBBBBB", start + 2000, "ETHUSDT", "B", "S5")
        await repo.insert_signal(first)
        await repo.insert_signal(second)
        await repo.record_veto_block(guard_name="G2", symbol="BTCUSDT", strategy_source="S3",
                                     event_ts_ms=start + 3000, message="feed unhealthy")
        await repo.record_veto_block(guard_name="G2", symbol="ETHUSDT", strategy_source="S5",
                                     event_ts_ms=start + 4000, message="stale snapshot")
        await repo.record_veto_block(guard_name="G9", symbol="SOLUSDT", strategy_source="S2",
                                     event_ts_ms=end + 1000, message="outside day")
        await repo.record_error_event(severity="ERROR", source="app.bot", exception_type="ValueError",
                                     message="evaluation failed", event_ts_ms=start + 5000)
        await repo.record_error_event(severity="CRITICAL", source="app.main", exception_type="RuntimeError",
                                     message="startup failure", event_ts_ms=end + 1000)
        await repo.record_outcome(first.signal_id, 1.2, recorded_ts_ms=start + 6000)
        await repo.record_outcome(second.signal_id, -0.2, recorded_ts_ms=start + 7000)

        report_date, message = await build_daily_report(repo, ASSUMPTIONS, now)
        assert report_date == day
        assert "Signals emitted: 2" in message
        assert "A+ : 0" in message and "A  : 1" in message and "B  : 1" in message
        assert "By strategy: S1=0, S2=0, S3=1, S4=0, S5=1" in message
        assert "BTCUSDT=1" in message and "ETHUSDT=1" in message
        assert "Vetoes blocked: 2" in message
        assert "G2 (2)" in message
        assert "Errors: 1" in message and "ERROR=1, CRITICAL=0" in message
        assert "Outcomes recorded: 2" in message
        assert "Wins:  1" in message and "Losses: 1" in message
        assert "Realized R sum: +1.00" in message
        assert "+1.00R × ₹5000 = +₹5,000" in message
        assert "Win rate: 50.0%" in message
        assert "Avg R:    +0.50" in message
        assert "Profit factor: 6.00" in message
        assert "ID: DR-20261008" in message
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_empty_report_says_no_outcomes_and_uses_na_metrics(tmp_path):
    repo = SignalRepository(tmp_path / "empty.db")
    await repo.connect()
    try:
        day, message = await build_daily_report(repo, ASSUMPTIONS, datetime(2026, 10, 8, 16, tzinfo=timezone.utc))
        assert "No outcomes recorded yet." in message
        assert "Win rate: N/A" in message
        assert "Avg R:    N/A" in message
        assert "Profit factor: N/A" in message
        assert f"ID: DR-{day:%Y%m%d}" in message
    finally:
        await repo.close()


def test_ist_bounds_and_next_report_boundary():
    utc_time = datetime(2026, 10, 8, 18, 29, tzinfo=timezone.utc)
    day, start, end = ist_day_bounds(utc_time)
    assert day.isoformat() == "2026-10-08"
    assert end - start == 86_400_000
    local_before = datetime(2026, 10, 8, 23, 58, tzinfo=ZoneInfo("Asia/Kolkata"))
    local_after = datetime(2026, 10, 8, 23, 59, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert next_daily_report_time(local_before).strftime("%Y-%m-%d %H:%M") == "2026-10-08 23:59"
    assert next_daily_report_time(local_after).strftime("%Y-%m-%d %H:%M") == "2026-10-09 23:59"


@pytest.mark.asyncio
async def test_report_generation_failure_is_logged_and_does_not_escape(monkeypatch, caplog):
    stop = asyncio.Event()
    fixed_now = datetime(2026, 10, 8, 23, 59, tzinfo=ZoneInfo("Asia/Kolkata"))

    class FixedDateTime:
        @staticmethod
        def now(_tz=None):
            return fixed_now

    async def fail_report(*_args, **_kwargs):
        stop.set()
        raise RuntimeError("report database unavailable")

    monkeypatch.setattr(reports_module, "datetime", FixedDateTime)
    monkeypatch.setattr(reports_module, "next_daily_report_time", lambda now: now)
    monkeypatch.setattr(reports_module, "build_daily_report", fail_report)
    await reports_module.run_daily_report_loop(object(), object(), ASSUMPTIONS, stop)
    assert "daily report generation failed" in caplog.text
    assert any(getattr(record, "context", {}).get("error_type") == "RuntimeError"
               for record in caplog.records)
