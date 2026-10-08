"""Binance USDⓈ-M public WebSocket client.

Only the public streams enumerated in spec section 3 are subscribed
to: aggTrade, markPrice, kline_<interval>, depth20@100ms, bookTicker.
No user-data stream, no listenKey, no API key.

Reconnect behavior: on disconnect, reconnect with exponential backoff
+ jitter, resubscribe to all previously-subscribed streams, and signal
a resync requirement upward (the caller — data/snapshot.py — is
responsible for treating post-reconnect data as needing a fresh
baseline, e.g. re-fetching the order book snapshot via REST, since a
depth WS stream is a delta stream that requires a REST snapshot to
seed).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.errors import WebSocketDisconnectedError
from app.core.logging import get_logger
from app.core.models import FeedHealth

logger = get_logger(__name__)

_ALLOWED_STREAM_SUFFIXES = ("@aggTrade", "@markPrice", "@bookTicker", "@depth20@100ms")


def _is_allowed_stream(stream: str) -> bool:
    if any(stream.endswith(suffix) for suffix in _ALLOWED_STREAM_SUFFIXES):
        return True
    # kline_<interval> streams look like "<symbol>@kline_5m"
    if "@kline_" in stream:
        return True
    return False


@dataclass
class WebSocketClientConfig:
    base_ws_url: str  # e.g. "wss://fstream.binance.com/stream"
    max_reconnect_backoff_s: float = 30.0
    base_backoff_ms: int = 500
    jitter_ms: int = 250
    max_reconnects_per_window: int = 5
    reconnect_window_s: float = 300.0


@dataclass
class _ReconnectTracker:
    """Tracks reconnect events in a rolling window for guard G2."""

    window_s: float
    events: list[float] = field(default_factory=list)

    def record(self, now_s: float) -> None:
        self.events.append(now_s)
        cutoff = now_s - self.window_s
        self.events = [e for e in self.events if e >= cutoff]

    def count_in_window(self, now_s: float) -> int:
        cutoff = now_s - self.window_s
        return sum(1 for e in self.events if e >= cutoff)


class BinanceWebSocketClient:
    """Manages a single combined-stream WebSocket connection with
    reconnect/backoff and per-stream health tracking.

    Usage: subscribe to streams up front via `streams`, then iterate
    `messages()` which yields (stream_name, payload_dict) tuples
    indefinitely, transparently reconnecting on disconnect.
    """

    def __init__(self, config: WebSocketClientConfig, streams: list[str]) -> None:
        invalid = [s for s in streams if not _is_allowed_stream(s)]
        if invalid:
            raise ValueError(f"disallowed WebSocket streams requested: {invalid}")
        self._config = config
        self._streams = streams
        self._reconnect_tracker = _ReconnectTracker(window_s=config.reconnect_window_s)
        self._last_message_ts_ms: dict[str, int] = {}
        self._connected = False
        self._reconnect_count_total = 0
        self._on_resync_required: Callable[[], None] | None = None

    def set_resync_callback(self, callback: Callable[[], None]) -> None:
        """Registered by the snapshot layer: called whenever a
        reconnect completes, signaling that delta-based state (e.g.
        order book) must be re-seeded from a fresh REST snapshot."""
        self._on_resync_required = callback

    @property
    def is_connected(self) -> bool:
        return self._connected

    def feed_health(self, stream: str, as_of_ts_ms: int) -> FeedHealth:
        last = self._last_message_ts_ms.get(stream, 0)
        return FeedHealth(
            symbol=stream.split("@")[0].upper(),
            stream=stream,
            last_message_received_ts_ms=last,
            reconnect_count_window=self._reconnect_tracker.count_in_window(time.time()),
            is_connected=self._connected,
        )

    def _stream_url(self) -> str:
        joined = "/".join(self._streams)
        return f"{self._config.base_ws_url}?streams={joined}"

    def _backoff_delay_s(self, attempt: int) -> float:
        base = self._config.base_backoff_ms * (2 ** (attempt - 1)) / 1000.0
        jitter = random.uniform(0, self._config.jitter_ms) / 1000.0
        return min(base + jitter, self._config.max_reconnect_backoff_s)

    async def messages(self) -> AsyncIterator[tuple[str, dict]]:
        attempt = 0
        while True:
            try:
                async with websockets.connect(self._stream_url(), ping_interval=20) as ws:
                    self._connected = True
                    attempt = 0
                    logger.info("websocket connected", extra={"context": {"streams": self._streams}})
                    async for raw in ws:
                        try:
                            envelope = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("dropped malformed WS message (non-JSON)")
                            continue
                        stream = envelope.get("stream")
                        payload = envelope.get("data")
                        if stream is None or payload is None:
                            logger.warning("dropped WS envelope missing stream/data", extra={"context": {"envelope": envelope}})
                            continue
                        self._last_message_ts_ms[stream] = int(time.time() * 1000)
                        yield stream, payload
            except (ConnectionClosed, OSError) as exc:
                self._connected = False
                attempt += 1
                self._reconnect_count_total += 1
                self._reconnect_tracker.record(time.time())
                delay = self._backoff_delay_s(attempt)
                logger.warning(
                    "websocket disconnected, reconnecting",
                    extra={"context": {"attempt": attempt, "delay_s": delay, "error": str(exc)}},
                )
                await asyncio.sleep(delay)
                if self._on_resync_required is not None:
                    self._on_resync_required()
                continue
