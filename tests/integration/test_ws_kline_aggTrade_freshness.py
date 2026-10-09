from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bot import LiveSnapshotCache
from app.config import load_all
from app.core.math import OHLC
from app.core.models import SymbolKlines
from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig

CFG = load_all()


def _kline_payload(close_time: int, *, closed: bool = True) -> dict:
    return {"k": {"t": close_time - 299_999, "T": close_time, "o": "100", "h": "101",
                    "l": "99", "c": "100.5", "v": "10", "V": "6", "x": closed}}


def _agg_payload(trade_id: int, trade_time: int) -> dict:
    return {"a": trade_id, "p": "100.5", "q": "0.25", "m": False, "T": trade_time}


def _cache() -> LiveSnapshotCache:
    cache = LiveSnapshotCache(CFG, SimpleNamespace())
    cache.data["BTCUSDT"] = {
        "klines": {"5m": SymbolKlines("BTCUSDT", "5m", [
            OHLC(open=100, high=101, low=99, close=100, volume=10, close_time_ms=1_000)
        ])},
        "depth": None, "ticker": None, "trades": [], "derivatives": None, "oi": None, "error": None,
    }
    return cache


def test_kline_and_aggtrade_messages_update_cache_and_receive_timestamps(monkeypatch):
    now = {"value": 10_000}
    monkeypatch.setattr("app.bot.now_ms", lambda: now["value"])
    cache = _cache()

    cache._apply_message("BTCUSDT@KLINE_5M", _kline_payload(9_900))
    cache._apply_message("BTCUSDT@AGGTRADE", _agg_payload(1, 9_950))
    cache._apply_message("BTCUSDT@BOOKTICKER", {"s": "BTCUSDT", "b": "100", "B": "1", "a": "101", "A": "1", "E": 9_999})

    ages = cache.diagnostic_ages_ms("BTCUSDT", as_of_ts_ms=10_000)
    assert ages["kline_age_ms"] == 0
    assert ages["taker_age_ms"] == 0
    assert len(cache.data["BTCUSDT"]["trades"]) == 1
    assert cache.data["BTCUSDT"]["ticker"].best_bid == 100
    assert cache.data["BTCUSDT"]["received"]["btcusdt@kline_5m"] == 10_000
    assert cache.data["BTCUSDT"]["received"]["btcusdt@aggtrade"] == 10_000


def test_repeated_kline_and_aggtrade_updates_keep_ages_under_five_seconds(monkeypatch):
    clock = {"value": 1_000_000}
    monkeypatch.setattr("app.bot.now_ms", lambda: clock["value"])
    cache = _cache()

    for index in range(30):
        clock["value"] += 1_000
        cache._apply_message("btcusdt@kline_5m", _kline_payload(clock["value"] - 100, closed=False))
        cache._apply_message("btcusdt@aggTrade", _agg_payload(index + 1, clock["value"]))
        ages = cache.diagnostic_ages_ms("BTCUSDT", as_of_ts_ms=clock["value"])
        assert ages["kline_age_ms"] < 5_000
        assert ages["taker_age_ms"] < 5_000


@pytest.mark.asyncio
async def test_silent_required_stream_triggers_reconnect():
    client = BinanceWebSocketClient(
        WebSocketClientConfig("wss://example.test/stream"),
        ["btcusdt@aggTrade", "btcusdt@kline_5m", "btcusdt@bookTicker"],
    )

    class FakeWS:
        def __init__(self):
            self.closed = False
            self.close_calls = []

        async def close(self, **kwargs):
            self.closed = True
            self.close_calls.append(kwargs)

    ws = FakeWS()
    await asyncio.wait_for(client._monitor_stream_health(ws, interval_s=0.001, silent_s=0.001), timeout=1)
    assert ws.closed
    assert ws.close_calls[0]["code"] == 4000
