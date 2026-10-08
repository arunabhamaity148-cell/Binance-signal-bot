"""Non-blocking ERROR/CRITICAL log bridge to a separate Telegram chat.

The handler enqueues records only; a background asyncio worker persists
all records and sends at most one Telegram notification per five minutes
per process. The rate-limit clock intentionally resets on restart. Any
persistence or Telegram failure is written to stderr and never propagated
back into the application logger or market-data loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app.database.repository import SignalRepository
from app.telegram.queue import TelegramQueue, TelegramLimitsConfig

IST = ZoneInfo("Asia/Kolkata")
_TELEGRAM_API = "https://api.telegram.org"
_STOP = object()


@dataclass(frozen=True)
class _ErrorRecord:
    created_ts_ms: int
    severity: str
    source: str
    exception_type: str
    message: str
    context: str
    frames: tuple[str, str, str]
    should_notify: bool


def format_error_notification(item: _ErrorRecord) -> str:
    when = datetime.fromtimestamp(item.created_ts_ms / 1000, timezone.utc).astimezone(IST)
    return (
        f"🚨 ERROR | {when:%Y-%m-%d %H:%M:%S} IST\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Source: {item.source}\n"
        f"❌ Type: {item.exception_type}\n"
        f"💬 Message: {item.message}\n"
        f"📎 Context: {item.context}\n"
        "🔍 Frames:\n"
        f"   {item.frames[0]}\n"
        f"   {item.frames[1]}\n"
        f"   {item.frames[2]}\n"
        "⏱️ Rate limit: 1 error / 5 min\n"
        f"ID: ERR-{when:%Y%m%d-%H%M%S}"
    )


class ErrorNotifier(logging.Handler):
    """Persists every qualifying record and asynchronously notifies a separate chat."""

    def __init__(
        self,
        repository: SignalRepository,
        *,
        bot_token: str,
        chat_id: str,
        dry_run: bool,
        telegram_limits: TelegramLimitsConfig,
        client: httpx.AsyncClient | None = None,
        monotonic_fn=time.monotonic,
        queue_size: int = 512,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self.repository = repository
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.telegram_queue = TelegramQueue(telegram_limits)
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._monotonic = monotonic_fn
        self._last_notification_s: float | None = None
        self._rate_lock = threading.Lock()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, queue_size))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task | None = None
        self._active = False
        self._limited_count = 0
        self._enqueued_count = 0

    async def start(self) -> None:
        if self._active:
            return
        self._loop = asyncio.get_running_loop()
        self._worker = asyncio.create_task(self._run(), name="telegram-error-notifier")
        logging.getLogger().addHandler(self)
        self._active = True

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR or not self._active or self._loop is None:
            return
        try:
            item = self._snapshot(record)
            self._loop.call_soon_threadsafe(self._enqueue, item)
        except Exception as exc:  # logging handlers must never break the caller
            self._stderr(f"error notifier enqueue failed: {type(exc).__name__}: {exc}")

    def _snapshot(self, record: logging.LogRecord) -> _ErrorRecord:
        timestamp_ms = int(record.created * 1000)
        secret = os.getenv("TELEGRAM_BOT_TOKEN", "")
        message = record.getMessage()
        if secret and len(secret) >= 6:
            message = message.replace(secret, "[REDACTED]")
        context = getattr(record, "context", {})
        context = context if isinstance(context, dict) else {}
        context_parts = [f"{key}={context[key]}" for key in ("symbol", "strategy", "strategy_source") if context.get(key)]
        context_text = ", ".join(context_parts) or "—"
        exception_type = "LogRecord"
        frames: list[str] = []
        if record.exc_info:
            exception = record.exc_info[1]
            exception_type = type(exception).__name__ if exception is not None else "Exception"
            frames = [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in traceback.extract_tb(record.exc_info[2])[-3:]]
        frames = (frames + ["(frame unavailable)"] * 3)[:3]
        now = self._monotonic()
        with self._rate_lock:
            should_notify = self._last_notification_s is None or now - self._last_notification_s >= 300.0
            if should_notify:
                self._last_notification_s = now
            else:
                self._limited_count += 1
        return _ErrorRecord(timestamp_ms, record.levelname, record.name, exception_type,
                            message[:2000], context_text[:500], tuple(frames), should_notify)

    def _enqueue(self, item: _ErrorRecord) -> None:
        try:
            self._queue.put_nowait(item)
            self._enqueued_count += 1
        except asyncio.QueueFull:
            self._stderr("error notifier queue full; ERROR/CRITICAL event was not persisted")

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                payload = json.dumps({"message": item.message, "context": item.context,
                                      "frames": item.frames}, ensure_ascii=False)
                try:
                    await self.repository.record_error_event(
                        severity=item.severity, source=item.source,
                        exception_type=item.exception_type, message=payload,
                        event_ts_ms=item.created_ts_ms,
                    )
                except Exception as exc:
                    self._stderr(f"error event persistence failed: {type(exc).__name__}: {exc}")
                if item.should_notify:
                    await self._notify(item)
            finally:
                self._queue.task_done()

    async def _notify(self, item: _ErrorRecord) -> None:
        if self.dry_run or not self.bot_token or not self.chat_id:
            return
        try:
            message = format_error_notification(item)
            await self.telegram_queue.acquire_send_slot(self.chat_id)
            response = await self._client.post(
                f"{_TELEGRAM_API}/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message},
            )
            response.raise_for_status()
            if not response.json().get("ok", False):
                raise RuntimeError("Telegram API response did not report ok=true")
        except Exception as exc:  # a Telegram outage must never affect the bot
            self._stderr(f"Telegram error notification failed: {type(exc).__name__}: {exc}")

    async def flush(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if not self._active:
            await self._client.aclose()
            return
        logging.getLogger().removeHandler(self)
        self._active = False
        await self._queue.join()
        await self._queue.put(_STOP)
        if self._worker is not None:
            await self._worker
        await self._client.aclose()

    @property
    def stats(self) -> dict[str, int]:
        return {"enqueued": self._enqueued_count, "rate_limited": self._limited_count,
                "queue_depth": self._queue.qsize()}

    @staticmethod
    def _stderr(message: str) -> None:
        try:
            sys.stderr.write(f"[error-notifier] {message}\n")
            sys.stderr.flush()
        except Exception:
            pass
