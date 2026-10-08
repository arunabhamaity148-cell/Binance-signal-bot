"""Read-only Telegram operator commands with strict chat authorization."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from app.core.logging import get_logger
from app.telegram.reports import ist_day_bounds

logger = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")
COMMAND_NAMES = ("start", "help", "status", "signals", "today", "open", "pairs", "strategies", "vetoes", "delta")


@dataclass
class CommandContext:
    repository: Any
    config: Any
    allowed_chat_ids: frozenset[str]
    state_provider: Callable[[], dict] | None = None
    delta_products_provider: Callable[[], dict] | None = None
    started_monotonic: float = 0.0


def _state(ctx: CommandContext) -> dict:
    try:
        value = ctx.state_provider() if ctx.state_provider else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _frame(title: str, body: list[str], ident: str) -> str:
    return "\n".join(["╔═══════════════════════════════════╗", f"║  {title:<33}║", "╚═══════════════════════════════════╝", *body, "━━━━━━━━━━━━━━━━━━━━━━━━━━━", ident])


async def handle_start(ctx: CommandContext) -> str:
    return _frame("🚀 ARUN ALPHA SIGNALS", ["Signal-only crypto intelligence.", "Public Binance market data · advisory use only.", "Use /help to see every command."], "ID: CMD-START")


async def handle_help(ctx: CommandContext) -> str:
    body = ["📚 OPERATOR COMMANDS"] + [f"/{name} — {description}" for name, description in {
        "start": "welcome and quick intro", "help": "list all commands", "status": "runtime health", "signals": "last 10 signals",
        "today": "today's paper statistics", "open": "active paper signals", "pairs": "tracked pair status",
        "strategies": "learn S1–S5", "vetoes": "learn G1–G15", "delta": "Delta conversion guide",
    }.items()]
    return _frame("📚 HELP", body, "ID: CMD-HELP")


async def handle_status(ctx: CommandContext) -> str:
    state = _state(ctx)
    uptime = max(0, int(time.monotonic() - ctx.started_monotonic)) if ctx.started_monotonic else 0
    last_message_ts_ms=state.get("last_message_ts_ms")
    ws=state.get("ws")
    if last_message_ts_ms is None and ws is not None:
        last_message_ts_ms=getattr(ws, "last_message_ts_ms", None)
        if last_message_ts_ms is None:
            timestamps=getattr(ws, "_last_message_ts_ms", {})
            if isinstance(timestamps, dict):
                last_message_ts_ms=max(timestamps.values(), default=None)
    feed_lag=format_feed_lag(last_message_ts_ms)
    return _frame("📡 BOT STATUS", [f"🟢 State: {state.get('status', 'running')}", f"🔌 WS: {state.get('ws_status', 'unknown')}", f"📊 Symbols: {len(ctx.config.enabled_symbols())}", f"⏱️ Uptime: {uptime // 3600}h {(uptime % 3600) // 60}m", f"🐢 Feed lag: {feed_lag}"], "ID: CMD-STATUS")


def format_feed_lag(last_message_ts_ms: int | None, *, now_ts_ms: int | None = None) -> str:
    """Format age of the latest WS receipt; never use event or historical-series age."""
    if last_message_ts_ms is None:
        return "⚪ N/A"
    from app.core.time_utils import now_ms
    try:
        lag_ms=max(0, (now_ms() if now_ts_ms is None else int(now_ts_ms))-int(last_message_ts_ms))
    except (TypeError, ValueError):
        return "⚪ N/A"
    if lag_ms < 1000:
        return f"🟢 {lag_ms}ms"
    seconds=lag_ms/1000.0
    if lag_ms <= 5000:
        return f"🟡 {seconds:.1f}s"
    return f"🔴 {seconds:.1f}s"


async def handle_signals(ctx: CommandContext) -> str:
    rows = await ctx.repository.get_recent_signal_rows(limit=10)
    body = ["📈 LAST 10 SIGNALS"]
    body.extend([f"{row.signal_id} · {row.symbol} · {row.grade} · {row.direction}" for row in rows] or ["No signals recorded."])
    return _frame("📈 SIGNALS", body, "ID: CMD-SIGNALS")


async def handle_today(ctx: CommandContext) -> str:
    day, start, end = ist_day_bounds(datetime.now(IST))
    data = await ctx.repository.get_daily_report_data(start_ts_ms=start, end_ts_ms=end)
    outcomes = data["outcomes_today"]
    return _frame("📊 TODAY", [f"Date: {day:%Y-%m-%d} IST", f"Signals: {data['signals_total']}", f"Wins: {outcomes['wins']} · Losses: {outcomes['losses']} · Flats: {outcomes['flats']}", f"Avg R: {(outcomes['realized_r'] / outcomes['count']):+.2f}" if outcomes["count"] else "Avg R: N/A", "PnL: paper/manual outcomes only"], "ID: CMD-TODAY")


async def handle_open(ctx: CommandContext) -> str:
    rows = await ctx.repository.get_open_signals()
    body = ["💼 OPEN PAPER SIGNALS"]
    body.extend([f"{row.signal_id} · {row.symbol} · {row.grade} · {row.direction}" for row in rows] or ["No active signals."])
    return _frame("💼 OPEN", body, "ID: CMD-OPEN")


async def handle_pairs(ctx: CommandContext) -> str:
    statuses = _state(ctx).get("pair_status", {})
    body = ["🧭 TRACKED PAIRS"]
    body.extend([f"{symbol} · {statuses.get(symbol, 'enabled')}" for symbol in ctx.config.enabled_symbols()])
    return _frame("🧭 PAIRS", body, "ID: CMD-PAIRS")


async def handle_strategies(ctx: CommandContext) -> str:
    body = ["📚 STRATEGY GUIDE", "🔍 S1 — Liquidity Sweep & Reclaim", "Catches a stop sweep followed by a reclaim.", "📈 S2 — Volatility Compression → Breakout", "Waits for a tight range to break with volume.", "💰 S3 — Funding Crowding", "Flags extreme funding and crowded positioning.", "📊 S4 — OI Trend", "Tracks open-interest direction with price.", "🌐 S5 — OI Regime", "Classifies the broader open-interest regime."]
    return _frame("📚 STRATEGIES", body, "ID: CMD-STRATEGIES")


async def handle_vetoes(ctx: CommandContext) -> str:
    descriptions = [
        ("Data Integrity", "Missing, stale, out-of-order, or malformed data."),
        ("Feed Health", "WebSocket disconnect or feed gap > 10s."),
        ("Depth Collapse", "Order-book liquidity too thin."),
        ("Spread Explosion", "Bid-ask spread too wide."),
        ("OI Anomaly", "OI spike/crash > 8% in 5m."),
        ("Funding Extreme", "|funding z| > 3.5."),
        ("News Shock", "Tier-1 HIGH/CRITICAL event."),
        ("Volatility Flash", "Bar range > 4x ATR."),
        ("BTC Regime", "BTC trend against signal."),
        ("Orderbook Instability", "Crossed, empty, or one-sided book."),
        ("Execution Quality", "Estimated slippage too high."),
        ("Self-Consistency", "Strategy output mismatch."),
        ("OI Divergence", "Price and OI move opposite."),
        ("OI Stagnation", "Flat OI plus strong price move."),
        ("OI Percentile Extreme", "OI above 95th or below 5th percentile."),
    ]
    body = ["🛡️ VETO GUARD GUIDE"] + [f"🚫 G{i} — {name}: {explanation}" for i, (name, explanation) in enumerate(descriptions, 1)]
    return _frame("🛡️ VETOES", body, "ID: CMD-VETOES")


async def handle_delta(ctx: CommandContext) -> str:
    delta = ctx.config.delta.get("delta", {})
    fees = delta.get("fees", {})
    gst = float(fees.get("gst_multiplier", 1.0))
    maker = float(fees.get("maker_pct", 0.0))
    taker = float(fees.get("taker_pct", 0.0))
    body = ["🔁 SEMI-AUTOMATED CONVERTER", "Signal conversion only — manual execution.", "No Delta credentials or order API.", f"Maker: {maker:.3f}% · GST-inclusive: {maker * gst:.3f}%", f"Taker: {taker:.3f}% · GST-inclusive: {taker * gst:.3f}%", "SL and TP are separate reduce-only orders."]
    products = ctx.delta_products_provider() if ctx.delta_products_provider else {}
    if isinstance(products, dict) and products:
        body.append(f"Loaded public products: {len(products)}")
    return _frame("🔁 DELTA GUIDE", body, "ID: CMD-DELTA")


_HANDLERS = {
    "start": handle_start, "help": handle_help, "status": handle_status, "signals": handle_signals,
    "today": handle_today, "open": handle_open, "pairs": handle_pairs, "strategies": handle_strategies,
    "vetoes": handle_vetoes, "delta": handle_delta,
}


async def dispatch_command(text: str, *, chat_id: str, context: CommandContext) -> str | None:
    """Return a reply only for an authorized chat and recognized command."""
    if str(chat_id) not in context.allowed_chat_ids:
        return None
    command = (text or "").strip().split(maxsplit=1)[0].lower().split("@", 1)[0] if (text or "").strip() else ""
    if not command.startswith("/") or command[1:] not in _HANDLERS:
        return None
    try:
        return await _HANDLERS[command[1:]](context)
    except Exception as exc:
        logger.warning("telegram command failed", extra={"context": {"command": command, "error_type": type(exc).__name__}})
        return "⚠️ Command data is temporarily unavailable. The signal engine is unaffected."


async def run_command_polling(*, token: str, sender, context: CommandContext, stop_event: asyncio.Event,
                              poll_interval_s: float = 2.0) -> None:
    """Long-poll getUpdates and dispatch only authorized operator chats."""
    offset: int | None = None
    last_response = 0.0
    async with httpx.AsyncClient(timeout=15.0) as client:
        while not stop_event.is_set():
            try:
                params = {"timeout": 10}
                if offset is not None:
                    params["offset"] = offset
                response = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                response.raise_for_status()
                payload = response.json()
                for update in payload.get("result", []):
                    offset = int(update.get("update_id", 0)) + 1
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    text = message.get("text", "")
                    reply = await dispatch_command(text, chat_id=str(chat.get("id", "")), context=context)
                    if reply is None:
                        continue
                    wait = 2.0 - (time.monotonic() - last_response)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    result = await sender.send_text_message(reply, chat_id=str(chat["id"]))
                    last_response = time.monotonic()
                    if getattr(result, "outcome", None) is not None and str(getattr(result, "outcome", "")) not in {"SendOutcome.SENT", "SENT"}:
                        logger.warning("command reply delivery failed", extra={"context": {"detail": getattr(result, "detail", None)}})
                await asyncio.sleep(poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("telegram command polling failed", extra={"context": {"error_type": type(exc).__name__, "error": str(exc)}})
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
                except asyncio.TimeoutError:
                    pass
