"""Premium, copy-friendly Telegram signal formatting.

All actionable numeric values are wrapped in inline Markdown code so an
operator can tap-copy them in Telegram. The formatter remains fail-closed:
critical entry, stop, targets, Delta values, and the signal ID are never
silently removed to make an oversized message fit.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import SignalBotError
from app.core.time_utils import ms_to_minutes
from app.signals.models import Signal

MAX_MESSAGE_CHARS = 1024


class FormatterError(SignalBotError):
    """Raised when a critical signal message cannot fit Telegram's limit."""


@dataclass(frozen=True)
class DeliveryContext:
    news_state_label: str
    binance_state_label: str
    as_of_ts_ms: int


def _format_price(value: float) -> str:
    """Use two decimals for ordinary prices and preserve precision for small assets."""
    value = float(value)
    if abs(value) < 1:
        return f"{value:.8f}".rstrip("0").rstrip(".") or "0"
    return f"{value:.2f}"


def _copy(value: object) -> str:
    return f"`{value}`"


def _format_confidence_pct(confidence: float) -> str:
    return f"{confidence * 100:.0f}%"


def _format_rr(value: float) -> str:
    return f"{value:.1f}"


def _format_expiry_minutes(expiry_ts_ms: int, as_of_ts_ms: int) -> str:
    remaining_ms = max(0, expiry_ts_ms - as_of_ts_ms)
    return f"{int(round(ms_to_minutes(remaining_ms)))}m"


def _delta_lines(signal: Signal, *, compact: bool = False) -> list[str]:
    if not signal.delta_available:
        return ["⚠️ Delta prices unavailable — using Binance values only."]
    if compact:
        return [
            "━━━ DELTA EXECUTION ━━━",
            f"📌 {_copy(signal.delta_symbol or signal.symbol)} · 📦 Contracts: {_copy(_format_price(signal.delta_contracts or 0.0))}",
            f"🎯 {_copy(_format_price(signal.delta_entry_low or 0.0))} — {_copy(_format_price(signal.delta_entry_high or 0.0))}",
            f"🛑 {_copy(_format_price(signal.delta_stop_loss or 0.0))}",
            f"🎁 {_copy(_format_price(signal.delta_tp1 or 0.0))} / {_copy(_format_price(signal.delta_tp2 or 0.0))} / {_copy(_format_price(signal.delta_tp3 or 0.0))} / {_copy(_format_price(signal.delta_tp4 or 0.0))}",
            f"📐 Delta R:R: {_copy('1:' + _format_rr(signal.delta_rr_tp2 or 0.0))}",
            "⚠️ SL + TP = separate orders (`reduce_only`)",
        ]
    return [
        "━━━ DELTA EXECUTION ━━━",
        f"📌 {_copy(signal.delta_symbol or signal.symbol)}",
        f"📦 Contracts: {_copy(_format_price(signal.delta_contracts or 0.0))}",
        f"🎯 Entry: {_copy(_format_price(signal.delta_entry_low or 0.0))} — {_copy(_format_price(signal.delta_entry_high or 0.0))}",
        f"🛑 SL: {_copy(_format_price(signal.delta_stop_loss or 0.0))}",
        f"🎁 TP1: {_copy(_format_price(signal.delta_tp1 or 0.0))}   TP2: {_copy(_format_price(signal.delta_tp2 or 0.0))}",
        f"🎁 TP3: {_copy(_format_price(signal.delta_tp3 or 0.0))}   TP4: {_copy(_format_price(signal.delta_tp4 or 0.0))}",
        f"📐 Delta R:R (with GST): {_copy('1:' + _format_rr(signal.delta_rr_tp2 or 0.0))}",
        "⚠️ SL + TP = separate orders (`reduce_only`)",
    ]


def _build_message(signal: Signal, ctx: DeliveryContext, *, concise: bool = False, compact_delta: bool = False,
                   compact_trade: bool = False, include_news: bool = True) -> str:
    symbol = signal.symbol
    expiry = _format_expiry_minutes(signal.expiry_ts_ms, ctx.as_of_ts_ms)
    news = str(ctx.news_state_label).replace("\n", " ")[:72]
    feed = str(ctx.binance_state_label).replace("\n", " ")[:40]
    advisory = [
        "⚠️ ADVISORY ONLY — VERIFY SIZING",
        "⚠️ LEVERAGE NOT SET BY BOT",
    ]
    if concise:
        advisory = ["⚠️ ADVISORY ONLY — VERIFY SIZING", "⚠️ LEVERAGE NOT SET BY BOT"]
    if compact_trade:
        lines = [
            "╔═══════════════════════════════════╗",
            "║  🚀 CRYPTO SIGNAL · BINANCE       ║",
            "╚═══════════════════════════════════╝",
            f"💎 {symbol} · {signal.direction} · Grade {signal.grade} · {_copy(_format_confidence_pct(signal.confidence))}",
            f"💰 Entry: {_copy(_format_price(signal.entry_low))} — {_copy(_format_price(signal.entry_high))}",
            f"🛑 SL: {_copy(_format_price(signal.stop_loss))} · ⏳ {_copy(expiry)}",
            f"🎁 TP1: {_copy(_format_price(signal.tp1))} · TP2: {_copy(_format_price(signal.tp2))}",
            f"🎁 TP3: {_copy(_format_price(signal.tp3))} · TP4: {_copy(_format_price(signal.tp4))}",
            f"📐 R:R: {_copy('1:' + _format_rr(signal.rr_tp2))}",
            *advisory,
        ]
    else:
        lines = [
            "╔═══════════════════════════════════╗",
            "║  🚀 CRYPTO SIGNAL · BINANCE       ║",
            "╚═══════════════════════════════════╝",
            "",
            f"💎 {symbol} · {signal.direction}",
            f"🎖️ Grade: {signal.grade} · Confidence: {_copy(_format_confidence_pct(signal.confidence))}",
            "",
            "┌───────────────────────────────────┐",
            "│  💰 ENTRY ZONE                    │",
            f"│  🎯 {_copy(_format_price(signal.entry_low))} — {_copy(_format_price(signal.entry_high))}                │",
            "├───────────────────────────────────┤",
            "│  🛑 STOP LOSS                     │",
            f"│  ⛔ {_copy(_format_price(signal.stop_loss))}                          │",
            "├───────────────────────────────────┤",
            "│  🎁 TARGETS                       │",
            f"│  🥇 TP1: {_copy(_format_price(signal.tp1))}                     │",
            f"│  🥈 TP2: {_copy(_format_price(signal.tp2))}                     │",
            f"│  🥉 TP3: {_copy(_format_price(signal.tp3))}                     │",
            f"│  🏆 TP4: {_copy(_format_price(signal.tp4))}                     │",
            "├───────────────────────────────────┤",
            f"│  📐 R:R {_copy('1:' + _format_rr(signal.rr_tp2))} · ⏳ Expiry {_copy(expiry)}   │",
            "└───────────────────────────────────┘",
            "",
            *advisory,
        ]
    if include_news:
        lines.extend([
            f"📰 News: {news}  🏦 Feed: {feed}",
            f"🛡️ Veto: {signal.veto_state}",
        ])
    lines.extend(["", *_delta_lines(signal, compact=compact_delta), "", f"🆔 ID: {signal.signal_id}"])
    return "\n".join(lines)


def format_signal_message(signal: Signal, ctx: DeliveryContext) -> str:
    """Format a premium signal while preserving every actionable price value."""
    variants = (
        dict(concise=False, compact_delta=False, include_news=True),
        dict(concise=True, compact_delta=False, include_news=True),
        dict(concise=True, compact_delta=True, compact_trade=True, include_news=True),
        dict(concise=True, compact_delta=True, compact_trade=True, include_news=False),
    )
    for options in variants:
        message = _build_message(signal, ctx, **options)
        if len(message) <= MAX_MESSAGE_CHARS:
            return message
    raise FormatterError(
        f"signal {signal.signal_id}: critical signal values cannot fit within {MAX_MESSAGE_CHARS} characters"
    )
