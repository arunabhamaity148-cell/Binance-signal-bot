"""Telegram sender: delivers a formatted message via the Telegram Bot
API, with 429/retry_after handling.

Per spec section 15: "Telegram failure must NOT break the market-data
engine." This is enforced at the PUBLIC entry point
(`send_signal_message`): every exception this module can raise
internally (HTTP errors, timeouts, malformed responses) is caught,
logged, and converted into a `SendResult` with `success=False` —
`send_signal_message` itself never raises. The one deliberate exception
is `FormatterError` from formatter.py, which is raised BEFORE any
network call is attempted (it is a precondition failure — "we refuse to
send this" — not a delivery failure, and the caller must be able to
distinguish "we chose not to send" from "we tried and failed to send";
see SendResult.outcome).

This module never places, cancels, or modifies any trading order and
holds no exchange credential — it exists purely to deliver a
human-readable text message, consistent with the signal-only invariant
enforced elsewhere in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import httpx

from app.core.errors import TelegramDeliveryError, TelegramRateLimitError
from app.core.logging import get_logger
from app.signals.models import Signal
from app.telegram.formatter import DeliveryContext, FormatterError, format_signal_message
from app.telegram.queue import TelegramQueue

logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class SendOutcome(str, Enum):
    SENT = "SENT"
    REFUSED_BY_FORMATTER = "REFUSED_BY_FORMATTER"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"


@dataclass(frozen=True)
class SendResult:
    outcome: SendOutcome
    detail: str | None = None


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str
    dry_run: bool


class TelegramSender:
    """Wraps the actual Telegram Bot API HTTP call. `dry_run=True`
    (the default per .env.example's TELEGRAM_DRY_RUN=true) skips the
    real network call and returns success — the bot can run fully
    without a configured bot token, per spec section 18: "Telegram
    (optional — bot runs in dry-run without these)".
    """

    def __init__(
        self,
        credentials: TelegramCredentials,
        queue: TelegramQueue,
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 2,
    ) -> None:
        self._credentials = credentials
        self._queue = queue
        self._client = client or httpx.AsyncClient()
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._client.aclose()

    async def send_signal_message(self, signal: Signal, ctx: DeliveryContext) -> SendResult:
        """Public entry point. NEVER raises — every failure mode is
        caught and reported via SendResult. Callers (the main
        evaluation loop, not built in this batch) can log/alert on a
        failed SendResult, but a Telegram outage can never crash or
        interrupt the market-data engine that called this.

        Precondition: `signal.veto_state` must be "PASS". This is
        checked here (not just documented) so a BLOCKed signal can
        never physically reach the network call, regardless of what
        any upstream caller does or fails to do.
        """
        if signal.veto_state != "PASS":
            return SendResult(
                outcome=SendOutcome.PRECONDITION_FAILED,
                detail=f"refusing to send signal with veto_state={signal.veto_state!r}",
            )

        try:
            message = format_signal_message(signal, ctx)
        except FormatterError as exc:
            logger.error("formatter refused to produce message", extra={"context": {"signal_id": signal.signal_id, "error": str(exc)}})
            return SendResult(outcome=SendOutcome.REFUSED_BY_FORMATTER, detail=str(exc))

        try:
            await self._deliver(message)
            return SendResult(outcome=SendOutcome.SENT)
        except Exception as exc:  # noqa: BLE001 - the hard guarantee: never propagate
            logger.error(
                "telegram delivery failed; market-data engine unaffected",
                extra={"context": {"signal_id": signal.signal_id, "error": str(exc)}},
            )
            return SendResult(outcome=SendOutcome.DELIVERY_FAILED, detail=str(exc))

    async def _deliver(self, message: str) -> None:
        if self._credentials.dry_run:
            logger.info("dry-run: would send telegram message", extra={"context": {"chars": len(message)}})
            return

        await self._queue.acquire_send_slot(self._credentials.chat_id)

        url = f"{TELEGRAM_API_BASE}/bot{self._credentials.bot_token}/sendMessage"
        payload = {"chat_id": self._credentials.chat_id, "text": message}

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.post(url, json=payload, timeout=10.0)
            except httpx.TimeoutException as exc:
                raise TelegramDeliveryError(f"telegram request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise TelegramDeliveryError(f"telegram request failed: {exc}") from exc

            if resp.status_code == 429:
                retry_after_header = resp.headers.get("Retry-After")
                retry_after_s = float(retry_after_header) if retry_after_header else None
                if attempt > self._max_retries:
                    raise TelegramRateLimitError(
                        f"telegram rate limited after {attempt} attempts", retry_after_s=retry_after_s
                    )
                import asyncio

                await asyncio.sleep(retry_after_s if retry_after_s is not None else 1.0)
                continue

            if resp.status_code != 200:
                raise TelegramDeliveryError(f"telegram API returned status {resp.status_code}: {resp.text}")

            return
