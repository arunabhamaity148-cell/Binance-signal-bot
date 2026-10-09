from __future__ import annotations

from types import SimpleNamespace

from app.bot import LiveSnapshotCache
from app.config import load_all
from app.core.math import OHLC
from app.core.models import SymbolKlines


CFG = load_all()


def _cache() -> LiveSnapshotCache:
    cache = LiveSnapshotCache(CFG, SimpleNamespace())
    cache.data["BTCUSDT"] = {
        "klines": {
            "5m": SymbolKlines("BTCUSDT", "5m", [
                OHLC(open=100, high=101, low=99, close=100, volume=10, close_time_ms=1_000),
            ])
        },
        "depth": None,
        "ticker": None,
        "trades": [],
        "derivatives": None,
        "oi": None,
        "error": None,
    }
    return cache


def test_kline_age_does_not_use_rest_backfill_timestamp(monkeypatch):
    now = {"value": 10_000}
    monkeypatch.setattr("app.bot.now_ms", lambda: now["value"])
    cache = _cache()

    assert cache.diagnostic_ages_ms("BTCUSDT", as_of_ts_ms=10_000)["kline_age_ms"] is None

    cache._apply_message("btcusdt@kline_5m", {
        "k": {"t": 9_700, "T": 9_999, "o": "100", "h": "101", "l": "99",
              "c": "100.5", "v": "10", "V": "6", "x": False},
    })

    assert cache.diagnostic_ages_ms("BTCUSDT", as_of_ts_ms=10_000)["kline_age_ms"] == 0
