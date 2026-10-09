from __future__ import annotations

import logging

import pytest

from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig


class _FakeWS:
    def __init__(self) -> None:
        self.close_calls: list[dict] = []

    async def close(self, **kwargs) -> None:
        self.close_calls.append(kwargs)


@pytest.mark.asyncio
async def test_silent_kline_5m_forces_reconnect(caplog):
    client = BinanceWebSocketClient(
        WebSocketClientConfig("wss://example.test/ws"),
        ["btcusdt@kline_5m", "btcusdt@bookTicker"],
    )
    ws = _FakeWS()

    with caplog.at_level(logging.WARNING, logger="app.data.binance.websocket"):
        await client._monitor_stream_health(ws, interval_s=0.001, silent_s=0.001)

    assert ws.close_calls == [{"code": 4000, "reason": "silent streams: btcusdt@kline_5m"}]
    forced = next(record for record in caplog.records if record.getMessage() == "ws_forced_reconnect")
    assert forced.context == {
        "reason": "silent_stream",
        "stream": "btcusdt@kline_5m",
        "silent_s": 0,
    }
