from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import load_and_validate_all
from app.telegram.commands import CommandContext, dispatch_command
from app.telegram.error_notifier import _ErrorRecord, format_error_notification
from app.telegram.formatter import DeliveryContext, MAX_MESSAGE_CHARS, format_signal_message
from app.telegram.hourly_summary import format_hourly_summary, next_hourly_summary_time
from app.telegram.messages import format_startup_message
from app.telegram.reports import format_daily_report
from app.signals.models import Signal

IST = ZoneInfo("Asia/Kolkata")


def signal(**overrides):
    values = dict(signal_id="CSB-20261009-ABC123", created_ts_ms=0, symbol="BTCUSDT", direction="LONG", grade="A", confidence=0.84, strategy_source="S1", entry_low=62150.0, entry_high=62200.0, stop_loss=61750.0, tp1=62600.0, tp2=63000.0, tp3=63600.0, tp4=64400.0, rr_tp2=2.1, expiry_ts_ms=45 * 60_000, why_lines=["test"], veto_state="PASS", size_units_advisory=1.0, notional_usd_advisory=62175.0, meta={})
    values.update(overrides)
    return Signal(**values)


def test_zec_replaces_ton_and_config_validates():
    cfg = load_and_validate_all()
    symbols = cfg.enabled_symbols()
    assert "ZECUSDT" in symbols and "TONUSDT" not in symbols
    assert "ZECUSDT" in cfg.risk["symbol_tiers"]["small_caps"]


def test_premium_signal_wraps_copyable_numeric_values_and_keeps_id():
    message = format_signal_message(signal(), DeliveryContext("clear", "connected", 0))
    assert "🚀 CRYPTO SIGNAL · BINANCE" in message
    assert "`62150.00`" in message and "`61750.00`" in message and "`64400.00`" in message
    assert "`84%`" in message and "`1:2.1`" in message
    assert "🆔 ID: CSB-20261009-ABC123" in message
    assert "⚠️ ADVISORY ONLY — VERIFY SIZING" in message


def test_delta_block_is_copyable_and_preserves_separate_order_instruction():
    msg = format_signal_message(signal(delta_available=True, delta_symbol="BTCUSD", delta_contracts=50, delta_entry_low=62150.5, delta_entry_high=62200.5, delta_stop_loss=61750.5, delta_tp1=62600.5, delta_tp2=63000.5, delta_tp3=63600.5, delta_tp4=64400.5, delta_rr_tp2=2.0), DeliveryContext("clear", "connected", 0))
    assert "━━━ DELTA EXECUTION ━━━" in msg
    assert "📌 `BTCUSD`" in msg and "Contracts: `50.00`" in msg
    assert "`62150.50`" in msg and "`61750.50`" in msg
    assert "reduce_only" in msg


def test_oversized_news_is_clipped_without_dropping_critical_fields():
    msg = format_signal_message(signal(), DeliveryContext("N" * 5000, "connected", 0))
    assert len(msg) <= MAX_MESSAGE_CHARS
    for value in ("62150.00", "61750.00", "62600.00", "63000.00", "63600.00", "64400.00"):
        assert f"`{value}`" in msg
    assert "CSB-20261009-ABC123" in msg


def test_premium_startup_message_is_signal_only():
    msg = format_startup_message(symbols=["BTCUSDT"] * 20, delta_available=True)
    assert "ARUN ALPHA SIGNALS · ONLINE" in msg
    assert "no orders are placed" in msg
    assert "Active guards: G1, G2, G4, G5, G6, G8, G9" in msg


def test_daily_report_has_premium_frame_and_legacy_metrics():
    msg = format_daily_report(datetime(2026, 10, 9).date(), {"signals_total": 0, "signals_by_grade": {}, "signals_by_strategy": {}, "signals_by_symbol": {}, "vetoes_by_guard": {}, "errors_by_severity": {}, "outcomes_today": {"count": 0, "realized_r": 0.0, "wins": 0, "losses": 0, "flats": 0, "gross_wins_r": 0.0, "gross_losses_r": 0.0}, "cumulative_outcomes": {"count": 0, "realized_r": 0.0, "wins": 0, "losses": 0, "flats": 0, "gross_wins_r": 0.0, "gross_losses_r": 0.0}}, {"account_capital_inr": 200000, "account_capital_usd": 2400, "risk_per_trade_inr": 5000, "risk_per_trade_pct": 2.5, "assumed_leverage": 10, "mode": "paper"})
    assert "DAILY PAPER REPORT" in msg and "No outcomes recorded yet." in msg


def test_error_notification_has_premium_frame_and_diagnostic_id():
    stamp = int(datetime(2026, 10, 9, 12, tzinfo=IST).timestamp() * 1000)
    msg = format_error_notification(_ErrorRecord(stamp, "ERROR", "app.bot", "RuntimeError", "failed", "symbol=BTCUSDT", ("a.py:1", "b.py:2", "c.py:3"), True))
    assert "SYSTEM ERROR · SIGNAL BOT" in msg and "RuntimeError" in msg and "ID: ERR-" in msg


def test_hourly_boundary_aligns_to_next_top_of_hour():
    current = datetime(2026, 10, 9, 14, 37, 11, tzinfo=IST)
    assert next_hourly_summary_time(current) == datetime(2026, 10, 9, 15, 0, tzinfo=IST)


def test_hourly_summary_contains_requested_sections_and_operator_id():
    msg = format_hourly_summary(report_time=datetime(2026, 10, 9, 15, tzinfo=IST), tracked_symbols=20, active_symbols=19, signals_this_hour=3, vetoes_blocked=7, strategy_counts={"S1": 1, "S2": 0, "S3": 1, "S4": 0, "S5": 1}, veto_counts={"G2": 4, "G9": 3}, top_movers=[("BTCUSDT", 62150.0, 1.2)], last_event="No new events", ws_status="connected", feed_lag_ms=120, uptime_seconds=3660, open_signals=2, open_by_grade={"A+": 0, "A": 1, "B": 1})
    for section in ("Market Pulse", "Strategy Breakdown", "Top Movers Watched", "Veto Activity", "News Status", "Open Signals (Paper)", "System Health"):
        assert section in msg
    assert "ID: HS-20261009-15" in msg and "BTCUSDT" in msg


@pytest.mark.asyncio
async def test_commands_authorize_chat_and_handle_help_without_network():
    ctx = CommandContext(repository=object(), config=load_and_validate_all(), allowed_chat_ids=frozenset({"123"}))
    assert await dispatch_command("/help", chat_id="999", context=ctx) is None
    reply = await dispatch_command("/help", chat_id="123", context=ctx)
    assert reply is not None and "/status" in reply and "/delta" in reply


@pytest.mark.asyncio
async def test_unknown_command_is_ignored_and_status_is_read_only():
    ctx = CommandContext(repository=object(), config=load_and_validate_all(), allowed_chat_ids=frozenset({"123"}), state_provider=lambda: {"status": "running", "ws_status": "connected"})
    assert await dispatch_command("/delete_everything", chat_id="123", context=ctx) is None
    reply = await dispatch_command("/status", chat_id="123", context=ctx)
    assert "BOT STATUS" in reply and "connected" in reply
