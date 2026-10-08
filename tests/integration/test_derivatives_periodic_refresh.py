from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from app.bot import DERIVATIVES_HISTORY_WINDOW_MS, LiveSnapshotCache, SignalBot
from app.config import load_all
from app.core.models import DerivativesState, TimestampedValue
from app.core.time_utils import now_ms
from app.data.binance.models import RawFundingRate, RawLongShortRatio, RawOpenInterest


class _HistoryRest:
    def __init__(self, stop_event: asyncio.Event, as_of_ts_ms: int):
        self.stop_event = stop_event
        self.as_of_ts_ms = as_of_ts_ms
        self.calls = Counter()
        self.windows = []

    def _record(self, name, start_time_ms, end_time_ms):
        self.calls[name] += 1
        self.windows.append((start_time_ms, end_time_ms))
        assert end_time_ms - start_time_ms == DERIVATIVES_HISTORY_WINDOW_MS
        if self.calls["fundingRate"] == 3:
            self.stop_event.set()

    async def funding_rate(self, symbol, limit=100, *, start_time_ms=None, end_time_ms=None):
        self._record("fundingRate", start_time_ms, end_time_ms)
        if self.calls["fundingRate"] == 1:
            raise RuntimeError("temporary mocked funding outage")
        return [RawFundingRate(symbol, 0.0001, self.as_of_ts_ms - 1_000)]

    async def open_interest_hist(self, symbol, period, limit=30, *, start_time_ms=None, end_time_ms=None):
        self._record(f"openInterestHist:{period}", start_time_ms, end_time_ms)
        return [RawOpenInterest(symbol, 1_000_000.0, self.as_of_ts_ms - 1_000)]

    async def global_long_short_account_ratio(self, symbol, period, limit=30, *, start_time_ms=None, end_time_ms=None):
        self._record("globalLongShortAccountRatio", start_time_ms, end_time_ms)
        return [RawLongShortRatio(symbol, 1.1, self.as_of_ts_ms - 1_000)]

    async def taker_long_short_ratio(self, symbol, period, limit=30, *, start_time_ms=None, end_time_ms=None):
        self._record("takerlongshortRatio", start_time_ms, end_time_ms)
        return [RawLongShortRatio(symbol, 1.2, self.as_of_ts_ms - 1_000)]


@pytest.mark.asyncio
async def test_derivatives_periodic_refresh_runs_three_cycles_deduplicates_and_recovers(caplog):
    cfg = load_all()
    as_of = now_ms()
    stop = asyncio.Event()
    rest = _HistoryRest(stop, as_of)
    cache = LiveSnapshotCache(cfg, rest)
    old = TimestampedValue(value=900_000.0, event_ts_ms=as_of - 2 * DERIVATIVES_HISTORY_WINDOW_MS,
                           received_ts_ms=as_of - 2 * DERIVATIVES_HISTORY_WINDOW_MS)
    cache.data["BTCUSDT"] = {
        "derivatives": DerivativesState(
            symbol="BTCUSDT",
            funding_rate_history=[old],
            open_interest_history_5m=[old],
            open_interest_history_15m=[old],
            open_interest_history_1h=[old],
            open_interest_history_1d=[old],
            long_short_account_ratio_history=[old],
            taker_long_short_ratio_history=[old],
            premium_index_current=None,
        )
    }
    bot = SignalBot(cfg, 1000.0, rest_client=rest)
    bot.cache = cache
    bot.stop_event = stop

    task = asyncio.create_task(bot._refresh_derivatives_history_loop(interval_s=0.001))
    await asyncio.wait_for(task, timeout=2.0)

    assert rest.calls == Counter({
        "fundingRate": 3,
        "openInterestHist:5m": 3,
        "openInterestHist:15m": 3,
        "openInterestHist:1h": 3,
        "openInterestHist:1d": 3,
        "globalLongShortAccountRatio": 3,
        "takerlongshortRatio": 3,
    })
    assert len(rest.windows) == 21
    assert any(getattr(record, "context", {}).get("endpoint") == "fundingRate" for record in caplog.records)

    derivatives = cache.data["BTCUSDT"]["derivatives"]
    for series in (
        derivatives.open_interest_history_5m,
        derivatives.open_interest_history_15m,
        derivatives.open_interest_history_1h,
        derivatives.open_interest_history_1d,
        derivatives.long_short_account_ratio_history,
        derivatives.taker_long_short_ratio_history,
        derivatives.funding_rate_history,
    ):
        timestamps = [point.event_ts_ms for point in series]
        assert timestamps == sorted(set(timestamps))  # event-time idempotency across replays
        assert len(series) == 2  # old percentile point retained + one deduplicated fresh point

    assert derivatives.open_interest_history_1d[0] == old
