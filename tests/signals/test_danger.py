from __future__ import annotations

from app.core.models import Direction, MarketSnapshot, SymbolKlines
from app.signals.danger import assess_s1_invalidation, assess_s4_invalidation


def _snapshot_with_5m_close(close_price: float) -> MarketSnapshot:
    from app.core.math import OHLC

    bar = OHLC(open=close_price, high=close_price + 1, low=close_price - 1, close=close_price, volume=10, close_time_ms=1000)
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=1000,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=[bar])},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def _snapshot_with_1h_close(close_price: float) -> MarketSnapshot:
    from app.core.math import OHLC

    bar = OHLC(open=close_price, high=close_price + 1, low=close_price - 1, close=close_price, volume=10, close_time_ms=1000)
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=1000,
        klines={"1h": SymbolKlines(symbol="BTCUSDT", timeframe="1h", bars=[bar])},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def test_s1_long_invalidated_when_close_below_l_low():
    snapshot = _snapshot_with_5m_close(90.0)
    result = assess_s1_invalidation("sig1", Direction.LONG, l_high=None, l_low=95.0, snapshot=snapshot)
    assert result.is_invalidated is True


def test_s1_long_not_invalidated_when_close_above_l_low():
    snapshot = _snapshot_with_5m_close(96.0)
    result = assess_s1_invalidation("sig1", Direction.LONG, l_high=None, l_low=95.0, snapshot=snapshot)
    assert result.is_invalidated is False


def test_s1_short_invalidated_when_close_above_l_high():
    snapshot = _snapshot_with_5m_close(110.0)
    result = assess_s1_invalidation("sig1", Direction.SHORT, l_high=105.0, l_low=None, snapshot=snapshot)
    assert result.is_invalidated is True


def test_s1_short_not_invalidated_when_close_below_l_high():
    snapshot = _snapshot_with_5m_close(100.0)
    result = assess_s1_invalidation("sig1", Direction.SHORT, l_high=105.0, l_low=None, snapshot=snapshot)
    assert result.is_invalidated is False


def test_s1_no_bars_returns_not_invalidated():
    snapshot = MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=1000, klines={},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )
    result = assess_s1_invalidation("sig1", Direction.LONG, l_high=None, l_low=95.0, snapshot=snapshot)
    assert result.is_invalidated is False


def test_s4_long_invalidated_when_close_below_ema_slow():
    snapshot = _snapshot_with_1h_close(90.0)
    result = assess_s4_invalidation("sig1", Direction.LONG, ema_slow=95.0, snapshot=snapshot)
    assert result.is_invalidated is True


def test_s4_short_invalidated_when_close_above_ema_slow():
    snapshot = _snapshot_with_1h_close(110.0)
    result = assess_s4_invalidation("sig1", Direction.SHORT, ema_slow=105.0, snapshot=snapshot)
    assert result.is_invalidated is True


def test_s4_long_not_invalidated_when_close_above_ema_slow():
    snapshot = _snapshot_with_1h_close(100.0)
    result = assess_s4_invalidation("sig1", Direction.LONG, ema_slow=95.0, snapshot=snapshot)
    assert result.is_invalidated is False
