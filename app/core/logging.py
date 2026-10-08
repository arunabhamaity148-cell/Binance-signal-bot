"""Structured logging.

Two output modes, selected by the LOG_JSON env-derived setting in
config/system.yaml's sibling env var (LOG_JSON=true/false): plain
human-readable lines for local/dev use, or single-line JSON for
production log aggregation. Never logs secrets — see `redact()`,
which every logger call for external-facing data should pass through.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "token",
    "bot_token",
    "password",
    "private_key",
)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of `data` with any key that looks like a
    credential replaced by a fixed redaction marker. Never logs secrets
    (spec section 25)."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        lowered = k.lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            out[k] = "***REDACTED***"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload["context"] = redact(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"[{record.levelname}] {record.name}: {record.getMessage()}"
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            base += f" | {redact(extra)}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Idempotent logging configuration. Safe to call multiple times;
    only the first call takes effect unless `force` semantics are
    needed (not required by this system — configuration happens once
    at boot in main.py)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter() if json_output else _PlainFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_with_context(
    logger: logging.Logger, level: int, message: str, context: dict[str, Any] | None = None
) -> None:
    logger.log(level, message, extra={"context": context or {}})
