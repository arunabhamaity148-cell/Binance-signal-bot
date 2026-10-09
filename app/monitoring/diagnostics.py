"""Config-driven, log-only diagnostics for strategy, veto, and news decisions."""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)
_LEVELS = {"off", "summary", "verbose"}
_level = "summary"


def configure(level: str | None = None, *, enabled: bool | None = None) -> str:
    global _level
    raw = level if level is not None else os.getenv("DIAGNOSTIC_LEVEL", "summary")
    raw = str(raw).strip().lower()
    if enabled is False or os.getenv("DIAGNOSTIC_MODE", "true").strip().lower() in {"0", "false", "no"}:
        raw = "off"
    if raw not in _LEVELS:
        raise ValueError(f"diagnostic_level must be one of {sorted(_LEVELS)}, got {raw!r}")
    _level = raw
    return _level


def level() -> str:
    return _level


def _emit(event: str, payload: dict[str, Any], *, verbose_only: bool = False) -> None:
    if _level == "off" or (verbose_only and _level != "verbose"):
        return
    logger.info("%s | %s", event, json.dumps(payload, sort_keys=True, default=str))


def _reason_token(reason: str) -> str:
    """Keep operational diagnostics stable and human-readable even when a
    legacy caller still supplies a source-like branch string."""
    raw = str(reason).strip().lower()
    direct = {
        "if not compression:": "range_not_compressed",
        "if abs(oi_delta_5m) < noise_band:": "oi_delta_within_noise_band",
        "):": "data_stale_or_unavailable",
    }
    if raw in direct:
        return direct[raw]
    if re.fullmatch(r"[a-z][a-z0-9_]*", raw):
        return raw
    if "compression" in raw:
        return "range_not_compressed"
    if "oi_delta" in raw and "noise" in raw:
        return "oi_delta_within_noise_band"
    if "len(" in raw or "history" in raw or "candles" in raw:
        return "insufficient_history"
    if "stale" in raw or "fresh" in raw:
        return "data_stale_or_unavailable"
    return "strategy_prerequisite_failed"


def strategy(symbol: str, strategy_name: str, decision: str, reason: str, values: dict[str, Any] | None = None) -> None:
    _emit("diag_strategy", {"symbol": symbol, "strategy": strategy_name, "decision": decision, "reason": _reason_token(reason), **({"values": values} if values else {})}, verbose_only=False)


def veto(symbol: str, guard: str, decision: str, reason: str | None, values: dict[str, Any] | None = None) -> None:
    _emit("diag_veto", {"symbol": symbol, "guard": guard, "decision": decision, "reason": reason, **({"values": values} if values else {})}, verbose_only=(level() == "verbose" and False))


def news(symbol: str, source: str, decision: str, reason: str, values: dict[str, Any] | None = None) -> None:
    _emit("diag_news", {"symbol": symbol, "source": source, "decision": decision, "reason": reason, **({"values": values} if values else {})}, verbose_only=False)


def is_verbose() -> bool:
    return _level == "verbose"


# Configure from environment for direct strategy/engine callers; main boot
# reconfigures from validated system.yaml, including environment overrides.
configure()
