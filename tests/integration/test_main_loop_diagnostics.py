from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bot import SignalBot
from app.config import load_all
from app.core.models import TimestampedValue


class _Closable:
    async def close(self):
        pass


class _News:
    def build_news_state(self, _stamp):
        return SimpleNamespace(active_for=lambda _symbol: ())


class _WS:
    is_connected = True
    reconnect_count_total = 2


class _Cache:
    def __init__(self):
        self.ws = _WS()
        self.ws_task = asyncio.create_task(asyncio.Event().wait())
        self.data = {"BTCUSDT": {"error": None}}

    def is_snapshot_ready(self, _symbol):
        return True

    def snapshot_missing_components(self, _symbol):
        return []

    def diagnostic_ages_ms(self, _symbol, _stamp):
        return {"kline_age_ms": 100, "book_age_ms": 200, "deriv_oldest_age_ms": 300,
                "deriv_newest_age_ms": 250,
                "taker_age_ms": 400, "oi_age_ms": 500}

    def get_snapshot(self, symbol):
        return SimpleNamespace(symbol=symbol, snapshot_version="test-v1")

    async def refresh_live_oi(self):
        return None

    async def refresh_historical_derivatives(self):
        return None


@pytest.mark.asyncio
async def test_main_loop_emits_info_diagnostics_for_empty_strategy_cycle(monkeypatch, caplog):
    import logging
    import app.bot as bot_module

    cfg = load_all()
    bot = SignalBot(cfg, 2400.0, rest_client=_Closable(), repository=_Closable())
    bot.symbols = ["BTCUSDT"]
    bot.cache = _Cache()
    bot.news_engine = _News()
    stop = asyncio.Event()

    def no_candidates(_snapshot, _news, _strategy_cfg):
        stop.set()
        return []

    monkeypatch.setattr(bot_module, "generate_candidates_at_snapshot", no_candidates)
    with caplog.at_level(logging.INFO, logger="app.bot"):
        try:
            await bot.run(stop_event=stop)
        finally:
            await bot.shutdown()

    messages = [record.getMessage() for record in caplog.records]
    assert "main_loop_cycle_started" in messages
    assert "snapshot_check" in messages
    assert "strategy_eval" in messages
    assert "main_loop_tick" in messages
    assert "loop_health" in messages

    check = next(record.context for record in caplog.records if record.getMessage() == "snapshot_check")
    assert check["symbol"] == "BTCUSDT"
    assert check["ready"] is True
    assert check["missing"] == []
    assert (check["kline_age_ms"], check["book_age_ms"], check["deriv_oldest_age_ms"], check["deriv_newest_age_ms"], check["taker_age_ms"]) == (100, 200, 300, 250, 400)

    strategy = next(record.context for record in caplog.records if record.getMessage() == "strategy_eval")
    assert [strategy[f"s{i}_cand"] for i in range(1, 6)] == [0, 0, 0, 0, 0]
    tick = next(record.context for record in caplog.records if record.getMessage() == "main_loop_tick")
    assert tick["cycle"] == 1 and tick["ready"] == 1 and tick["total"] == 1
    assert tick["candidates"] == 0 and tick["signals"] == 0
    health = next(record.context for record in caplog.records if record.getMessage() == "loop_health")
    assert health["ws_reconnects_total"] == 2
    assert health["ws_connected"] is True


@pytest.mark.asyncio
async def test_reconnect_rehydration_reports_ready_and_stale_symbols(caplog):
    import logging

    from app.bot import LiveSnapshotCache

    cache = LiveSnapshotCache.__new__(LiveSnapshotCache)

    async def backfill(_symbols):
        return None

    cache.backfill = backfill
    cache.is_snapshot_ready = lambda symbol: symbol == "BTCUSDT"
    cache.snapshot_missing_components = lambda symbol: [] if symbol == "BTCUSDT" else ["missing=orderbook"]
    cache.diagnostic_ages_ms = lambda _symbol, _stamp: {
        "kline_age_ms": 100, "book_age_ms": 25_000, "deriv_age_ms": 300,
        "taker_age_ms": 400, "oi_age_ms": 500,
    }

    with caplog.at_level(logging.WARNING, logger="app.bot"):
        result = await cache._rehydrate_after_reconnect(["BTCUSDT", "ETHUSDT"])

    assert result is False
    complete = next(r.context for r in caplog.records if r.getMessage() == "ws_reconnect_complete")
    assert complete == {"rehydrated": 1, "stale": 1}
    stale = next(r.context for r in caplog.records if r.getMessage() == "ws_reconnect_no_rehydrate")
    assert stale["symbol"] == "ETHUSDT"
    assert stale["last_data_age_ms"] == 25_000
    assert stale["missing_components"] == ["missing=orderbook"]


def test_derivative_series_health_reports_oldest_and_newest_event_ages():
    from app.bot import LiveSnapshotCache

    cache = LiveSnapshotCache.__new__(LiveSnapshotCache)
    point_old = TimestampedValue(1.0, 1_000, 1_000)
    point_new = TimestampedValue(2.0, 9_000, 9_000)
    cache.data = {"ADAUSDT": {"derivatives": SimpleNamespace(
        funding_rate_history=[point_old, point_new], open_interest_history_5m=[],
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=[],
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[],
        premium_index_current=None,
    )}}

    assert cache.diagnostic_derivatives_health("ADAUSDT", 10_000) == {
        "symbol": "ADAUSDT", "oldest_age_ms": 9_000, "newest_age_ms": 1_000, "count": 2,
    }
