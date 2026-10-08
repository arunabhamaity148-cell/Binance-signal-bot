"""Hourly, database-backed Telegram summary reports."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.telegram.sender import SendOutcome

logger = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_STRATEGY_NAMES = {
    "S1": "Liquidity Sweep",
    "S2": "Volatility Compression",
    "S3": "Funding Crowding",
    "S4": "OI Trend",
    "S5": "OI Regime",
}


def next_hourly_summary_time(now: datetime) -> datetime:
    current = (now if now.tzinfo else now.replace(tzinfo=IST)).astimezone(IST)
    target = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return target


def _code(value: object) -> str:
    return f"`{value}`"


def format_hourly_summary(*, report_time: datetime, tracked_symbols: int, active_symbols: int,
                           signals_this_hour: int, vetoes_blocked: int,
                           strategy_counts: dict[str, int], veto_counts: dict[str, int],
                           top_movers: list[tuple[str, float | None, float | None]],
                           last_event: str, ws_status: str, feed_lag_ms: int | None,
                           uptime_seconds: float, open_signals: int,
                           open_by_grade: dict[str, int]) -> str:
    local = (report_time if report_time.tzinfo else report_time.replace(tzinfo=IST)).astimezone(IST)
    hours, remainder = divmod(max(0, int(uptime_seconds)), 3600)
    minutes = remainder // 60
    movers = top_movers or [("BTCUSDT", None, None), ("ETHUSDT", None, None), ("SOLUSDT", None, None)]
    mover_lines = []
    for symbol, price, pct in movers[:3]:
        price_text = "—" if price is None else f"${price:,.2f}"
        pct_text = "—" if pct is None else f"{pct:+.2f}%"
        mover_lines.append(f"   ├ {symbol}: {_code(price_text)} ({_code(pct_text)})")
    if mover_lines:
        mover_lines[-1] = mover_lines[-1].replace("├", "└", 1)
    guard_items = [(guard, count) for guard, count in sorted(veto_counts.items()) if count > 0]
    guard_lines = []
    for index in range(0, len(guard_items), 2):
        pair = guard_items[index:index + 2]
        body = "    ".join(f"{name}: {_code(count)}" for name, count in pair)
        guard_lines.append(f"   {'└' if index + 2 >= len(guard_items) else '├'} {body}")
    if not guard_lines:
        guard_lines = ["   └ None: `0`"]
    lines = [
        f"⏰ HOURLY SUMMARY — {local:%H}:00 IST",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 Market Pulse",
        f"   ├ Active symbols: {_code(f'{active_symbols}/{tracked_symbols}')}",
        f"   ├ Signals this hour: {_code(signals_this_hour)}",
        f"   └ Vetoes blocked: {_code(vetoes_blocked)}",
        "",
        "🎯 Strategy Breakdown",
    ]
    for index, strategy in enumerate(("S1", "S2", "S3", "S4", "S5")):
        branch = "└" if index == 4 else "├"
        lines.append(f"   {branch} {strategy} {_STRATEGY_NAMES[strategy]}: {_code(strategy_counts.get(strategy, 0))}")
    lines.extend(["", "🔥 Top Movers Watched", *mover_lines, "", "🛡️ Veto Activity", *guard_lines,
                  "", "📰 News Status", f"   └ Last event: {last_event or 'No new events'}", "",
                  "💼 Open Signals (Paper)", f"   └ {_code(open_signals)} active signals",
                  f"      (grade A+: {_code(open_by_grade.get('A+', 0))}, A: {_code(open_by_grade.get('A', 0))}, B: {_code(open_by_grade.get('B', 0))})",
                  "", "⚡ System Health", f"   ├ WS: {ws_status}",
                  f"   ├ Feed lag: {_code('unknown' if feed_lag_ms is None else str(feed_lag_ms) + 'ms')}",
                  f"   └ Uptime: {_code(f'{hours}h {minutes}m')}",
                  "", f"ID: HS-{local:%Y%m%d-%H}", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"])
    message = "\n".join(lines)
    if len(message) > 1024:
        # The summary is descriptive; preserve all counts while clipping only the event label.
        message = message.replace(last_event or "No new events", (last_event or "No new events")[:40])
    return message[:1024]


async def build_hourly_summary(repository, *, tracked_symbols: int, runtime_state: dict,
                               now: datetime | None = None) -> str:
    current = (now or datetime.now(IST)).astimezone(IST)
    end = current.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    data = await repository.get_hourly_summary_data(
        start_ts_ms=int(start.timestamp() * 1000), end_ts_ms=int(end.timestamp() * 1000),
        now_ts_ms=int(current.timestamp() * 1000),
    )
    return format_hourly_summary(report_time=end, tracked_symbols=tracked_symbols,
        active_symbols=int(runtime_state.get("active_symbols", 0)), signals_this_hour=data["signals_total"],
        vetoes_blocked=data["vetoes_total"], strategy_counts=data["signals_by_strategy"],
        veto_counts=data["vetoes_by_guard"], top_movers=runtime_state.get("top_movers", []),
        last_event=runtime_state.get("last_event", "No new events"),
        ws_status=runtime_state.get("ws_status", "unknown"), feed_lag_ms=runtime_state.get("feed_lag_ms"),
        uptime_seconds=float(runtime_state.get("uptime_seconds", 0)), open_signals=data["open_signals"],
        open_by_grade=data["open_by_grade"])


async def run_hourly_summary_loop(repository, sender, *, tracked_symbols_provider, runtime_state_provider,
                                  stop_event: asyncio.Event) -> None:
    """Schedule a summary at each top-of-hour IST boundary; all failures are contained."""
    while not stop_event.is_set():
        now = datetime.now(IST)
        target = next_hourly_summary_time(now)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, (target - now).total_seconds()))
            continue
        except asyncio.TimeoutError:
            pass
        try:
            state = runtime_state_provider() if callable(runtime_state_provider) else {}
            if asyncio.iscoroutine(state):
                state = await state
            count = tracked_symbols_provider() if callable(tracked_symbols_provider) else int(tracked_symbols_provider)
            if asyncio.iscoroutine(count):
                count = await count
            message = await build_hourly_summary(repository, tracked_symbols=int(count), runtime_state=state)
            result = await sender.send_text_message(message)
            if getattr(result, "outcome", None) != SendOutcome.SENT:
                logger.warning("hourly summary delivery did not succeed", extra={"context": {"detail": getattr(result, "detail", None)}})
        except Exception as exc:
            logger.warning("hourly summary failed", extra={"context": {"error_type": type(exc).__name__, "error": str(exc)}})
