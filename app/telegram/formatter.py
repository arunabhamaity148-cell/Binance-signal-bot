"""Telegram message formatter: exact signal format per spec section 15.

Format (exact, including emoji and spacing):

    🚨 SIGNAL | <SYMBOL> — <LONG|SHORT>
    ⚠️ ADVISORY ONLY — VERIFY ACCOUNT SIZING MANUALLY. No account
    state, fill, leverage, or liquidation distance is observed.
    🧠 Grade: <A+|A|B>   📊 Confidence: <pct>
    🎯 LIMIT ENTRY: <low> – <high>
    🛑 SL: <price>
    🎯 TP1: <p1>   🎯 TP2: <p2>
    🎯 TP3: <p3>   🎯 TP4: <p4>
    📐 R:R: 1 : <rr>   ⏳ Expiry: <n>m
    📰 News: <state>   🏦 Binance: <state>
    🛡️ Veto: <PASS|BLOCK>
    ID: <signal_id>

HARD RULE (spec section 15 + explicit instruction): the ADVISORY
warning line is NEVER dropped. If the message would exceed the
1024-character cap, every OTHER line is shortened/dropped first, in a
fixed, documented precedence order, before the warning is touched. If
even the signal_id line plus the warning alone cannot fit under 1024
characters, this module refuses to produce a message at all
(`FormatterError`) rather than ever send a signal without the warning
— the caller (queue.py) is required to treat that as a dropped
message, never a partially-correct one.

The veto_state precondition from signal_engine.py's docstring applies
here too: this formatter does not itself check veto_state == "PASS"
before formatting (callers — queue.py / sender.py — are responsible
for that precondition), since a BLOCKed signal might legitimately need
to be formatted for audit/logging purposes even though it must never
reach the sender.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import SignalBotError
from app.core.time_utils import ms_to_minutes
from app.signals.models import ADVISORY_WARNING, Signal

MAX_MESSAGE_CHARS = 1024


class FormatterError(SignalBotError):
    """Raised when a Signal cannot be formatted within MAX_MESSAGE_CHARS
    even after every droppable line has been dropped — i.e. the
    signal_id line + the ADVISORY warning alone do not fit. The caller
    MUST treat this as "do not send", never as a partial message."""


@dataclass(frozen=True)
class DeliveryContext:
    """System-health context the formatter needs but that doesn't live
    on the Signal object itself (Signal is pure trade content; these
    are operational status strings supplied by the caller at format
    time, e.g. from the news engine's and Binance feed health's
    current state)."""

    news_state_label: str
    binance_state_label: str
    as_of_ts_ms: int


def _format_price(value: float) -> str:
    """Trim to a readable number of decimals without losing
    significant precision for low-priced assets: fixed at 6 decimals,
    trailing zeros stripped, but never fewer than the integer part
    alone (i.e. '100' not '100.')."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _format_confidence_pct(confidence: float) -> str:
    return f"{confidence * 100:.0f}%"


def _format_rr(rr_tp2: float) -> str:
    return f"{rr_tp2:.1f}"


def _format_expiry_minutes(expiry_ts_ms: int, as_of_ts_ms: int) -> str:
    remaining_ms = max(0, expiry_ts_ms - as_of_ts_ms)
    minutes = int(round(ms_to_minutes(remaining_ms)))
    return f"{minutes}m"


def _build_lines(signal: Signal, ctx: DeliveryContext) -> list[str]:
    """Build every line of the message, in order. Each element of the
    returned list is one logical line as specified; some spec lines
    (grade/confidence, TP1/TP2, TP3/TP4, news/binance) pack two fields
    onto one physical line with triple-space separation, matching the
    example format in spec section 15 exactly.
    """
    direction_arrow = signal.direction
    header = f"🚨 SIGNAL | {signal.symbol} — {direction_arrow}"
    warning = f"⚠️ {signal.advisory_warning}"
    grade_conf = f"🧠 Grade: {signal.grade}   📊 Confidence: {_format_confidence_pct(signal.confidence)}"
    entry = f"🎯 LIMIT ENTRY: {_format_price(signal.entry_low)} – {_format_price(signal.entry_high)}"
    sl = f"🛑 SL: {_format_price(signal.stop_loss)}"
    tp12 = f"🎯 TP1: {_format_price(signal.tp1)}   🎯 TP2: {_format_price(signal.tp2)}"
    tp34 = f"🎯 TP3: {_format_price(signal.tp3)}   🎯 TP4: {_format_price(signal.tp4)}"
    rr_expiry = (
        f"📐 R:R: 1 : {_format_rr(signal.rr_tp2)}   "
        f"⏳ Expiry: {_format_expiry_minutes(signal.expiry_ts_ms, ctx.as_of_ts_ms)}"
    )
    news_binance = f"📰 News: {ctx.news_state_label}   🏦 Binance: {ctx.binance_state_label}"
    veto = f"🛡️ Veto: {signal.veto_state}"
    signal_id_line = f"ID: {signal.signal_id}"

    return [header, warning, grade_conf, entry, sl, tp12, tp34, rr_expiry, news_binance, veto, signal_id_line]


# Precedence order for what gets dropped FIRST when the message is too
# long, expressed as line indices into _build_lines' output, ordered
# least-essential-first. The warning (index 1) and signal_id (index
# 10) are never in this list — they are the two lines that must always
# survive (signal_id for audit traceability, warning per the hard
# rule). Everything else is droppable, in this order, before the
# warning would ever be touched.
_DROP_PRECEDENCE = [8, 6, 5, 3, 4, 2, 9, 7, 0]  # news/binance, tp3/4, tp1/2, entry, sl, grade/conf, veto, rr/expiry, header


def format_signal_message(signal: Signal, ctx: DeliveryContext) -> str:
    """Format `signal` into the exact Telegram message text.

    Raises FormatterError if the message cannot fit within
    MAX_MESSAGE_CHARS even after dropping every droppable line (i.e.
    header line + warning line + signal_id line alone, joined by
    newlines, still exceed the cap) — this should be effectively
    impossible given the warning's fixed, known length and a
    reasonable signal_id format, but the check exists so the hard rule
    ("if the warning alone cannot fit, DO NOT SEND") is enforced in
    code, not just in a comment.
    """
    lines = _build_lines(signal, ctx)
    message = "\n".join(lines)

    if len(message) <= MAX_MESSAGE_CHARS:
        return message

    # Drop lines in precedence order until it fits, never dropping the
    # warning (index 1) or the signal_id (index 10).
    remaining_indices = list(range(len(lines)))
    for drop_idx in _DROP_PRECEDENCE:
        if len(message) <= MAX_MESSAGE_CHARS:
            break
        if drop_idx in remaining_indices:
            remaining_indices.remove(drop_idx)
            message = "\n".join(lines[i] for i in remaining_indices)

    if len(message) <= MAX_MESSAGE_CHARS:
        return message

    # Even header + warning + signal_id alone don't fit: refuse to
    # produce a message. Never send a truncated/corrupted warning.
    minimal = "\n".join([lines[1], lines[10]])  # warning + signal_id only
    if len(minimal) > MAX_MESSAGE_CHARS:
        raise FormatterError(
            f"signal {signal.signal_id}: even the ADVISORY warning plus signal_id "
            f"line alone exceed {MAX_MESSAGE_CHARS} characters ({len(minimal)} chars); "
            f"refusing to produce a message without the advisory warning intact"
        )
    raise FormatterError(
        f"signal {signal.signal_id}: message could not be shortened to fit "
        f"{MAX_MESSAGE_CHARS} characters while preserving the ADVISORY warning "
        f"and signal_id; refusing to send a partial message"
    )
