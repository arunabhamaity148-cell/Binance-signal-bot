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
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.errors import WebSocketDisconnectedError
from app.core.logging import get_logger
from app.core.models import FeedHealth

logger = get_logger(__name__)

TESTNET_WS_BASE_URL = "wss://stream.binancefuture.com/stream"

_ALLOWED_STREAM_SUFFIXES = ("@aggTrade", "@markPrice", "@markPrice@1s", "@bookTicker", "@depth20@100ms")


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
    binance_env: str = "mainnet"

    def __post_init__(self) -> None:
        if self.binance_env not in {"mainnet", "testnet"}:
            raise ValueError("binance_env must be 'mainnet' or 'testnet'")
        if self.binance_env == "testnet":
            self.base_ws_url = TESTNET_WS_BASE_URL


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
        self._stream_aliases = {stream.lower(): stream for stream in streams}
        self._message_counts: dict[str, int] = {stream: 0 for stream in streams}
        self._reconnect_tracker = _ReconnectTracker(window_s=config.reconnect_window_s)
        self._last_message_ts_ms: dict[str, int] = {}
        self._connected = False
        self._reconnect_count_total = 0
        self._on_resync_required: Callable[[], object] | None = None
        self._last_resync_task: asyncio.Task | None = None
        self._last_connected_monotonic: float | None = None
        self._last_disconnect_monotonic: float | None = None
        self._subscriptions_acked = 0
        self._subscription_rejections: list[object] = []

    def set_resync_callback(self, callback: Callable[[], object]) -> None:
        """Registered by the snapshot layer: called whenever a
        reconnect completes, signaling that delta-based state (e.g.
        order book) must be re-seeded from a fresh REST snapshot."""
        self._on_resync_required = callback

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count_total(self) -> int:
        """Cumulative disconnect/reconnect attempts since this client started."""
        return self._reconnect_count_total

    @property
    def last_message_ts_ms(self) -> int | None:
        """Wall-clock timestamp of the most recently received WS message."""
        return max(self._last_message_ts_ms.values(), default=None)

    @property
    def message_counts(self) -> dict[str, int]:
        return dict(self._message_counts)

    async def _monitor_stream_health(self, ws, *, interval_s: float = 60.0, silent_s: float = 60.0) -> None:
        previous = dict(self._message_counts)
        while True:
            await asyncio.sleep(interval_s)
            now_ms = int(time.time() * 1000)
            silent_streams = []
            for configured_stream in self._streams:
                count = self._message_counts.get(configured_stream, 0)
                delta = count - previous.get(configured_stream, 0)
                previous[configured_stream] = count
                last = self._last_message_ts_ms.get(configured_stream)
                age = None if last is None else max(0, now_ms - last)
                logger.info("ws_stream_health", extra={"context": {
                    "stream": configured_stream,
                    "msgs_last_60s": delta,
                    "last_msg_age_ms": age,
                }})
                if delta == 0 and (age is None or age >= int(silent_s * 1000)):
                    logger.warning("ws_stream_silent", extra={"context": {
                        "stream": configured_stream,
                        "symbol": configured_stream.split("@", 1)[0].upper(),
                        "silent_s": int(silent_s),
                        "reconnect_triggered": self._is_critical_stream(configured_stream),
                    }})
                    silent_streams.append(configured_stream)
            critical_silent = [stream for stream in silent_streams if self._is_critical_stream(stream)]
            if critical_silent:
                for stream in critical_silent:
                    logger.warning("ws_forced_reconnect", extra={"context": {
                        "reason": "silent_stream",
                        "stream": stream,
                        "silent_s": int(silent_s),
                    }})
                logger.warning("ws_stream_reconnect_triggered", extra={"context": {
                    "silent_streams": critical_silent,
                    "reconnect_triggered": True,
                }})
                await ws.close(code=4000, reason=f"silent streams: {','.join(critical_silent[:3])}")
                return

    def _log_no_rehydrate(self) -> None:
        symbols=sorted({stream.split("@",1)[0].upper() for stream in self._streams})
        now_ms=int(time.time()*1000)
        for symbol in symbols:
            stamps=[stamp for stream,stamp in self._last_message_ts_ms.items()
                    if stream.split("@",1)[0].upper()==symbol]
            age=max(0,now_ms-max(stamps)) if stamps else None
            logger.warning("ws_reconnect_no_rehydrate",extra={"context":{"symbol":symbol,"last_data_age_ms":age}})

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
        parsed = urlsplit(self._config.base_ws_url)
        if parsed.hostname == "fstream.binance.com" and parsed.path.rstrip("/") == "/stream":
            # Binance split futures market streams into category paths.
            # /market/stream is the combined endpoint that supports the
            # SUBSCRIBE method for all requested market stream families.
            return urlunsplit(parsed._replace(path="/market/stream", query=""))
        return self._config.base_ws_url

    @staticmethod
    def _is_critical_stream(stream: str) -> bool:
        normalized = stream.lower()
        return normalized.endswith("@aggtrade") or normalized.endswith("@kline_5m")

    async def _send_subscribe(self, ws) -> None:
        request = {"method": "SUBSCRIBE", "params": list(self._streams), "id": 1}
        message = json.dumps(request, separators=(",", ":"))
        logger.info("ws_subscribe_send", extra={"context": {
            "raw_json": message,
            "params": list(self._streams),
            "requested": len(self._streams),
        }})
        # A few legacy unit-test sockets model only receive behavior. Binance's
        # real websocket always supplies send(), so keep those doubles usable.
        send = getattr(ws, "send", None)
        if send is not None:
            await send(message)

    def _handle_subscription_ack(self, message: dict) -> bool:
        if message.get("id") != 1 or ("result" not in message and "error" not in message):
            return False
        error = message.get("error")
        if error is None and message.get("result") is None:
            self._subscriptions_acked = len(self._streams)
            self._subscription_rejections = []
        else:
            self._subscriptions_acked = 0
            self._subscription_rejections = [error if error is not None else message.get("result")]
        logger.info("ws_subscribe_ack", extra={"context": {
            "requested": len(self._streams),
            "acked": self._subscriptions_acked,
            "rejected": list(self._subscription_rejections),
            "response": message,
        }})
        return True

    def _stream_from_raw_payload(self, payload: dict) -> str | None:
        symbol = str(payload.get("s") or payload.get("k", {}).get("s") or "").lower()
        event = payload.get("e")
        suffix = {
            "aggTrade": "@aggTrade",
            "markPriceUpdate": "@markPrice@1s",
            "depthUpdate": "@depth20@100ms",
            "bookTicker": "@bookTicker",
        }.get(event)
        if event == "kline":
            interval = payload.get("k", {}).get("i")
            suffix = f"@kline_{interval}" if interval else None
        if not symbol or suffix is None:
            return None
        candidate = f"{symbol}{suffix}"
        return self._stream_aliases.get(candidate.lower(), candidate)

    def _backoff_delay_s(self, attempt: int) -> float:
        base = self._config.base_backoff_ms * (2 ** (attempt - 1)) / 1000.0
        jitter = random.uniform(0, self._config.jitter_ms) / 1000.0
        return min(base + jitter, self._config.max_reconnect_backoff_s)

    def _endpoint_groups(self) -> list[tuple[str, list[str]]]:
        """Return endpoint groups required by Binance's current WS split."""
        parsed = urlsplit(self._stream_url())
        if parsed.hostname != "fstream.binance.com" or parsed.path.rstrip("/") != "/market/stream":
            return [(self._config.base_ws_url, list(self._streams))]
        public = [stream for stream in self._streams if stream.lower().endswith(("@bookticker", "@depth20@100ms"))]
        market = [stream for stream in self._streams if stream not in public]
        endpoints = []
        if public:
            endpoints.append((urlunsplit(parsed._replace(path="/public/stream", query="")), public))
        if market:
            endpoints.append((self._config.base_ws_url, market))
        return endpoints

    async def messages(self) -> AsyncIterator[tuple[str, dict]]:
        groups = self._endpoint_groups()
        if len(groups) == 1:
            async for item in self._messages_single():
                yield item
            return

        queue: asyncio.Queue[tuple[str, dict] | BaseException | None] = asyncio.Queue()
        children: list[BinanceWebSocketClient] = []

        async def pump(endpoint: str, streams: list[str]) -> None:
            child_config = WebSocketClientConfig(
                endpoint,
                max_reconnect_backoff_s=self._config.max_reconnect_backoff_s,
                base_backoff_ms=self._config.base_backoff_ms,
                jitter_ms=self._config.jitter_ms,
                max_reconnects_per_window=self._config.max_reconnects_per_window,
                reconnect_window_s=self._config.reconnect_window_s,
                binance_env="mainnet",
            )
            child = BinanceWebSocketClient(child_config, streams)
            child.set_resync_callback(self._on_resync_required) if self._on_resync_required else None
            children.append(child)
            try:
                async for stream, payload in child._messages_single():
                    self._connected = child.is_connected
                    self._message_counts[stream] = self._message_counts.get(stream, 0) + 1
                    if child._last_message_ts_ms.get(stream) is not None:
                        self._last_message_ts_ms[stream] = child._last_message_ts_ms[stream]
                    await queue.put((stream, payload))
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                await queue.put(exc)

        tasks = [asyncio.create_task(pump(endpoint, streams), name=f"binance-ws-{index}")
                 for index, (endpoint, streams) in enumerate(groups)]
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _messages_single(self) -> AsyncIterator[tuple[str, dict]]:
        attempt = 0
        while True:
            try:
                async with websockets.connect(self._stream_url(), ping_interval=20) as ws:
                    self._connected = True
                    await self._send_subscribe(ws)
                    connected_at=time.monotonic()
                    reconnect_duration_ms=None
                    if self._last_disconnect_monotonic is not None:
                        reconnect_duration_ms=int((connected_at-self._last_disconnect_monotonic)*1000)
                        task=self._last_resync_task
                        cache_rehydrated=False
                        if task is not None and task.done():
                            try: cache_rehydrated=bool(task.result())
                            except (asyncio.CancelledError,Exception): cache_rehydrated=False
                        symbols_resubscribed=len({stream.split("@",1)[0].upper() for stream in self._streams})
                        logger.info("ws_reconnect_recovered",extra={"context":{"symbols_resubscribed":symbols_resubscribed,
                            "cache_rehydrated":cache_rehydrated,"rehydration_pending":bool(task is not None and not task.done()),
                            "duration_ms":reconnect_duration_ms,"total":self._reconnect_count_total}})
                        self._last_disconnect_monotonic=None
                    self._last_connected_monotonic=connected_at
                    attempt = 0
                    logger.info("websocket connected", extra={"context": {"streams": self._streams}})
                    health_task = asyncio.create_task(self._monitor_stream_health(ws), name="binance-ws-stream-health")
                    try:
                        async for raw in ws:
                            try:
                                envelope = json.loads(raw)
                            except json.JSONDecodeError:
                                logger.warning("dropped malformed WS message (non-JSON)")
                                continue
                            if self._handle_subscription_ack(envelope):
                                continue
                            stream = envelope.get("stream")
                            payload = envelope.get("data") if stream is not None else envelope
                            if stream is None:
                                stream = self._stream_from_raw_payload(payload)
                            if stream is None or payload is None:
                                logger.warning("dropped WS envelope missing stream/data", extra={"context": {"envelope": envelope}})
                                continue
                            configured_stream = self._stream_aliases.get(str(stream).lower(), str(stream))
                            received_ts_ms = int(time.time() * 1000)
                            self._last_message_ts_ms[configured_stream] = received_ts_ms
                            self._message_counts[configured_stream] = self._message_counts.get(configured_stream, 0) + 1
                            logger.debug("ws_msg_received", extra={"context": {
                                "stream": configured_stream,
                                "symbol": configured_stream.split("@", 1)[0].upper(),
                                "count": self._message_counts[configured_stream],
                            }})
                            yield configured_stream, payload
                    finally:
                        health_task.cancel()
                        await asyncio.gather(health_task, return_exceptions=True)
            except (ConnectionClosed, OSError) as exc:
                self._connected = False
                attempt += 1
                self._reconnect_count_total += 1
                self._reconnect_tracker.record(time.time())
                delay = self._backoff_delay_s(attempt)
                disconnected_at=time.monotonic()
                connection_duration_ms=(int((disconnected_at-self._last_connected_monotonic)*1000)
                                         if self._last_connected_monotonic is not None else 0)
                self._last_disconnect_monotonic=disconnected_at
                self._last_resync_task=None
                logger.warning("ws_reconnect_event",extra={"context":{"attempt":attempt,
                    "total":self._reconnect_count_total,"duration_ms":connection_duration_ms,
                    "duration_kind":"previous_connection_uptime","error":str(exc)}})
                logger.warning(
                    "websocket disconnected, reconnecting",
                    extra={"context": {"attempt": attempt, "total": self._reconnect_count_total,
                        "duration_ms":connection_duration_ms,"delay_s": delay, "error": str(exc)}},
                )
                await asyncio.sleep(delay)
                if self._on_resync_required is not None:
                    result=self._on_resync_required()
                    if isinstance(result,asyncio.Task): self._last_resync_task=result
                    elif asyncio.iscoroutine(result): self._last_resync_task=asyncio.create_task(result)
                    if self._last_resync_task is None: self._log_no_rehydrate()
                else:
                    self._log_no_rehydrate()
                continue
