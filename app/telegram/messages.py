"""Small premium Telegram message builders used by the live bot."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def format_startup_message(*, symbols: list[str], delta_available: bool, uptime_seconds: float = 0.0) -> str:
    now = datetime.now(IST)
    delta = "enabled" if delta_available else "unavailable · Binance-only fallback"
    return "\n".join([
        "╔═══════════════════════════════════╗",
        "║  🚀 ARUN ALPHA SIGNALS · ONLINE   ║",
        "╚═══════════════════════════════════╝",
        f"🟢 Signal-only engine started · {now:%Y-%m-%d %H:%M} IST",
        f"📊 Universe: `{len(symbols)}` symbols",
        f"🔁 Delta converter: {delta}",
        "⚠️ Advisory messages only · no orders are placed",
        "🛡️ Guards: G1–G15 active",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🆔 START-{now:%Y%m%d-%H%M}",
    ])


def format_shutdown_message(*, reason: str = "operator stop") -> str:
    now = datetime.now(IST)
    return "\n".join([
        "╔═══════════════════════════════════╗",
        "║  🛑 ARUN ALPHA SIGNALS · OFFLINE  ║",
        "╚═══════════════════════════════════╝",
        f"Reason: {reason}",
        f"🆔 STOP-{now:%Y%m%d-%H%M}",
    ])
