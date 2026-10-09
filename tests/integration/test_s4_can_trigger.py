from __future__ import annotations

from app.config import load_all
from app.core.math import OHLC
from app.core.models import DerivativesState, TimestampedValue
from app.strategies.s4_oi_trend import S4OiTrend
from tests.strategies.test_s4_oi_trend import _build_snapshot, _empty_news, _staircase_uptrend_bars

CFG = load_all().strategy


def _hh_hl_bars():
    bars = []
    timestamp = 0
    price = 100.0
    for _ in range(10):
        bars.append(OHLC(price, price + 1, price - 1, price + 0.5, 100, timestamp))
        price += 0.5
        timestamp += 900_000
    for cycle in range(4):
        low = price + cycle * 2
        high = low + 8
        for j in range(4):
            close = low + (high - low) * (j + 1) / 4
            bars.append(OHLC(close - 0.5, close + 0.8, close - 0.8, close, 100, timestamp))
            timestamp += 900_000
        for j in range(4):
            close = high - (high - (low + 2)) * (j + 1) / 4
            bars.append(OHLC(close + 0.5, close + 0.8, close - 0.8, close, 100, timestamp))
            timestamp += 900_000
    return bars


def test_s4_can_trigger_from_trend_pullback_structure_and_oi():
    bars_5m = _staircase_uptrend_bars(70, 100.0, 0.5, 300_000)
    bars_1h = _staircase_uptrend_bars(80, 100.0, 1.5, 3_600_000)
    bars_4h = _staircase_uptrend_bars(60, 100.0, 2.0, 14_400_000)
    bars_15m = _hh_hl_bars()
    from app.core.math import ema_series
    ema_fast = ema_series([bar.close for bar in bars_1h], 21)[-1]
    for j in range(3):
        old = bars_5m[-3 + j]
        close = ema_fast - 0.25 + j * 0.2
        bars_5m[-3 + j] = OHLC(close - 0.05, close + 0.35, close - 0.35, close, 100, old.close_time_ms)
    as_of = bars_5m[-1].close_time_ms + 1
    oi = [
        TimestampedValue(1_000_000 + i * 1_000, as_of - (49 - i) * 300_000, as_of - (49 - i) * 300_000)
        for i in range(49)
    ]
    oi.append(TimestampedValue(1_060_000, as_of, as_of))
    derivatives = DerivativesState("BTCUSDT", [], oi, [], [], [], [], [], None)
    snapshot = _build_snapshot(
        bars_5m=bars_5m, bars_15m=bars_15m, bars_1h=bars_1h, bars_4h=bars_4h,
        derivatives=derivatives, as_of_ts_ms=as_of,
    )
    result = S4OiTrend().evaluate(snapshot, _empty_news(as_of), CFG)
    assert result, "S4 must construct a candidate from a documented synthetic setup"
    assert result[0].strategy_source == "S4"
