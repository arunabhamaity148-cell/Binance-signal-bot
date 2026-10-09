from __future__ import annotations

import logging

from app.backtest.engine import generate_candidates_at_snapshot
from app.config import load_all
from app.monitoring import diagnostics
from tests.integration.test_s4_can_trigger import _empty_news, _build_snapshot, _hh_hl_bars
from tests.strategies.test_s4_oi_trend import _staircase_uptrend_bars
from app.core.math import OHLC, ema_series
from app.core.models import DerivativesState, TimestampedValue


def _valid_s4_snapshot():
    bars_5m = _staircase_uptrend_bars(70, 100.0, 0.5, 300_000)
    bars_1h = _staircase_uptrend_bars(80, 100.0, 1.5, 3_600_000)
    bars_4h = _staircase_uptrend_bars(60, 100.0, 2.0, 14_400_000)
    bars_15m = _hh_hl_bars()
    ema_fast = ema_series([bar.close for bar in bars_1h], 21)[-1]
    for j in range(3):
        old = bars_5m[-3 + j]
        close = ema_fast - 0.25 + j * 0.2
        bars_5m[-3 + j] = OHLC(close - 0.05, close + 0.35, close - 0.35, close, 100, old.close_time_ms)
    as_of = bars_5m[-1].close_time_ms + 1
    oi = [TimestampedValue(1_000_000 + i * 1_000, as_of - (49 - i) * 300_000, as_of - (49 - i) * 300_000) for i in range(49)]
    oi.append(TimestampedValue(1_060_000, as_of, as_of))
    return _build_snapshot(bars_5m=bars_5m, bars_15m=bars_15m, bars_1h=bars_1h, bars_4h=bars_4h,
                           derivatives=DerivativesState("BTCUSDT", [], oi, [], [], [], [], [], None), as_of_ts_ms=as_of), as_of


def test_s4_dispatcher_invokes_evaluate_in_trending(caplog):
    diagnostics.configure("verbose")
    snapshot, as_of = _valid_s4_snapshot()
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        candidates = generate_candidates_at_snapshot(snapshot, _empty_news(as_of), load_all().strategy, regime="TRENDING")
    assert any(record.getMessage().find('"strategy": "S4"') >= 0 for record in caplog.records)
    assert any(candidate.strategy_source == "S4" for candidate in candidates)
