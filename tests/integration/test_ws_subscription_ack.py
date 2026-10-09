from __future__ import annotations

import json

import pytest

from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig


class _FakeWS:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = iter(messages)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return json.dumps(next(self.messages))
        except StopIteration:
            raise StopAsyncIteration


class _Connector:
    def __init__(self, socket: _FakeWS) -> None:
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_market_ws_subscribe_ack_contains_all_canonical_streams(monkeypatch, caplog):
    import app.data.binance.websocket as ws_module

    streams = [
        "btcusdt@aggTrade",
        "btcusdt@depth20@100ms",
        "btcusdt@bookTicker",
        "btcusdt@markPrice@1s",
        "btcusdt@kline_5m",
        "btcusdt@kline_15m",
        "btcusdt@kline_1h",
        "btcusdt@kline_4h",
        "btcusdt@kline_1d",
    ]
    sockets: list[_FakeWS] = []
    captured_url: list[str] = []

    def fake_connect(url, **_kwargs):
        captured_url.append(url)
        stream = "btcusdt@bookTicker" if url.endswith("/public/stream") else "btcusdt@aggTrade"
        socket = _FakeWS([
            {"result": None, "id": 1},
            {"stream": stream, "data": {"e": "bookTicker" if "bookTicker" in stream else "aggTrade", "s": "BTCUSDT"}},
        ])
        sockets.append(socket)
        return _Connector(socket)

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    client = BinanceWebSocketClient(WebSocketClientConfig("wss://fstream.binance.com/stream"), streams)
    gen = client.messages()
    with caplog.at_level("INFO", logger="app.data.binance.websocket"):
        await anext(gen)
    await gen.aclose()

    requests = [json.loads(socket.sent[0]) for socket in sockets]
    requested = {stream for request in requests for stream in request["params"]}
    assert captured_url == ["wss://fstream.binance.com/public/stream", "wss://fstream.binance.com/market/stream"]
    assert requested == set(streams)
    assert all(request["method"] == "SUBSCRIBE" and request["id"] == 1 for request in requests)
    acks = [record for record in caplog.records if record.getMessage() == "ws_subscribe_ack"]
    assert sorted((record.context["requested"], record.context["acked"], record.context["rejected"]) for record in acks) == [(2, 2, []), (7, 7, [])]
