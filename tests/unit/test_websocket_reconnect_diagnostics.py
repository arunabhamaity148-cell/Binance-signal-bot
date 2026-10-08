from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig


class _Socket:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Future()


class _Connector:
    def __init__(self, *, fail: bool):
        self.fail = fail

    async def __aenter__(self):
        if self.fail:
            raise OSError("synthetic disconnect before handshake")
        await asyncio.sleep(0.01)
        return _Socket()

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_reconnect_logs_total_and_cache_rehydration_after_success(monkeypatch, caplog):
    import app.data.binance.websocket as ws_module

    calls = 0

    def fake_connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Connector(fail=calls == 1)

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    client = BinanceWebSocketClient(
        WebSocketClientConfig("wss://example.test/stream", base_backoff_ms=1, jitter_ms=0),
        ["btcusdt@aggTrade", "btcusdt@bookTicker", "ethusdt@aggTrade"],
    )
    callback_calls = 0

    def rehydrate():
        nonlocal callback_calls
        callback_calls += 1
        return asyncio.create_task(asyncio.sleep(0, result=True))

    client.set_resync_callback(rehydrate)
    gen = client.messages()
    with caplog.at_level(logging.INFO, logger="app.data.binance.websocket"):
        pending = asyncio.create_task(anext(gen))
        try:
            for _ in range(100):
                if any(r.getMessage() == "ws_reconnect_recovered" for r in caplog.records):
                    break
                await asyncio.sleep(0.01)
            recovered = [r for r in caplog.records if r.getMessage() == "ws_reconnect_recovered"]
            assert recovered
            context = recovered[0].context
            assert context["symbols_resubscribed"] == 2
            assert context["total"] == 1
            assert context["duration_ms"] >= 0
            assert context["cache_rehydrated"] is True
            assert client.reconnect_count_total == 1
            assert callback_calls == 1
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await gen.aclose()
