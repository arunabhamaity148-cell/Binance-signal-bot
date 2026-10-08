"""Database-backed daily paper report, scheduled for 23:59 Asia/Kolkata."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.database.repository import SignalRepository
from app.telegram.sender import SendOutcome

logger = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def ist_day_bounds(now: datetime | None = None) -> tuple[date, int, int]:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    local = current.astimezone(IST)
    start = datetime.combine(local.date(), time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    return local.date(), int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def next_daily_report_time(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    local = now.astimezone(IST)
    target = datetime.combine(local.date(), time(23, 59), tzinfo=IST)
    return target if local <= target else target + timedelta(days=1)


def _signed(value: float, places: int = 2) -> str:
    return f"{value:+.{places}f}"


def _profit_factor(data: dict) -> str:
    gross_loss = abs(float(data["gross_losses_r"]))
    gross_win = float(data["gross_wins_r"])
    if data["count"] == 0:
        return "N/A"
    if gross_loss == 0:
        return "∞ (no losses)" if gross_win > 0 else "N/A"
    return f"{gross_win / gross_loss:.2f}"


def format_daily_report(report_date: date, data: dict, assumptions: dict) -> str:
    grades = data["signals_by_grade"]
    strategies = data["signals_by_strategy"]
    symbols = data["signals_by_symbol"]
    guards = data["vetoes_by_guard"]
    severities = data["errors_by_severity"]
    today = data["outcomes_today"]
    cumulative = data["cumulative_outcomes"]
    total_vetoes = sum(guards.values())
    total_errors = sum(severities.values())
    top = sorted(guards.items(), key=lambda item: (-item[1], int(item[0][1:])))[:2]
    top_text = ", ".join(f"{name} ({count})" for name, count in top) or "None (0), None (0)"
    by_guard = ", ".join(f"G{i}={guards.get(f'G{i}', 0)}" for i in range(1, 16))
    by_strategy = ", ".join(f"S{i}={strategies.get(f'S{i}', 0)}" for i in range(1, 6))
    by_symbol = ", ".join(f"{symbol}={count}" for symbol, count in sorted(symbols.items())) or "—"
    risk_inr = int(assumptions["risk_per_trade_inr"])
    pnl_inr = float(today["realized_r"]) * risk_inr
    if cumulative["count"]:
        win_rate = f"{cumulative['wins'] / cumulative['count'] * 100:.1f}%"
        avg_r = _signed(cumulative["realized_r"] / cumulative["count"])
    else:
        win_rate, avg_r = "N/A", "N/A"
    lines = [
        f"📊 DAILY REPORT — {report_date:%Y-%m-%d} (IST)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 Signals emitted: {data['signals_total']}",
        f"   ├ A+ : {grades.get('A+', 0)}",
        f"   ├ A  : {grades.get('A', 0)}",
        f"   └ B  : {grades.get('B', 0)}",
        f"🎯 By strategy: {by_strategy}",
        f"🔎 By symbol: {by_symbol}",
        f"🛡️ Vetoes blocked: {total_vetoes}",
        f"   ├ By guard: {by_guard}",
        f"   └ Top: {top_text}",
        f"❌ Errors: {total_errors}",
        f"   └ By severity: ERROR={severities.get('ERROR', 0)}, CRITICAL={severities.get('CRITICAL', 0)}",
        f"📝 Outcomes recorded: {today['count']}",
    ]
    if today["count"] == 0:
        lines.append("   No outcomes recorded yet.")
    lines.extend([
        f"   ├ Wins:  {today['wins']}",
        f"   ├ Losses: {today['losses']}",
        f"   ├ Flats: {today['flats']}",
        f"   └ Realized R sum: {_signed(today['realized_r'])}",
        "💹 Paper P&L (assumed):",
        f"   └ {_signed(today['realized_r'])}R × ₹{risk_inr} = {'+' if pnl_inr >= 0 else '−'}₹{abs(pnl_inr):,.0f}",
        "🎯 Cumulative (paper):",
        f"   ├ Win rate: {win_rate}",
        f"   ├ Avg R:    {avg_r}",
        f"   └ Profit factor: {_profit_factor(cumulative)}",
        f"⚠️ Assumptions: ₹{int(assumptions['account_capital_inr'])} capital, ₹{risk_inr}/trade, {assumptions['assumed_leverage']}x leverage",
        "   (operator-set; bot does not execute)",
        f"ID: DR-{report_date:%Y%m%d}",
    ])
    return "\n".join(lines)


async def build_daily_report(repository: SignalRepository, assumptions: dict,
                             now: datetime | None = None) -> tuple[date, str]:
    report_date, start_ms, end_ms = ist_day_bounds(now)
    data = await repository.get_daily_report_data(start_ts_ms=start_ms, end_ts_ms=end_ms)
    return report_date, format_daily_report(report_date, data, assumptions)


async def run_daily_report_loop(repository: SignalRepository, sender, assumptions: dict,
                                stop_event: asyncio.Event) -> None:
    """Wait independently for each 23:59 IST boundary and fail soft on report errors."""
    while not stop_event.is_set():
        now = datetime.now(IST)
        target = next_daily_report_time(now)
        delay = max(0.0, (target - now).total_seconds())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            report_date, message = await build_daily_report(repository, assumptions)
            result = await sender.send_text_message(message)
            if getattr(result, "outcome", None) != SendOutcome.SENT:
                logger.warning("daily report delivery did not succeed", extra={"context": {"report_date": str(report_date), "detail": getattr(result, "detail", None)}})
        except Exception as exc:  # report failure is logged only; scheduler continues
            logger.warning("daily report generation failed", extra={"context": {"error_type": type(exc).__name__, "error": str(exc)}})
