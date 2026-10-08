from __future__ import annotations

import pytest

from app.core.errors import DataIntegrityError
from app.data.binance.models import RawBookTicker, RawDepthLevel, RawDepthSnapshot
from app.data.orderbook import depth_and_ticker_to_orderbook_state


def _depth(symbol="BTCUSDT", n_levels=5):
    bids = [RawDepthLevel(price=100.0 - i * 0.1, quantity=1.0) for i in range(n_levels)]
    asks = [RawDepthLevel(price=100.1 + i * 0.1, quantity=1.0) for i in range(n_levels)]
    return RawDepthSnapshot(symbol=symbol, bids=bids, asks=asks, event_time_ms=1000, last_update_id=1)


def _ticker(symbol="BTCUSDT"):
    return RawBookTicker(
        symbol=symbol, best_bid=100.0, best_bid_qty=1.0, best_ask=100.1, best_ask_qty=1.0, event_time_ms=1000
    )


def test_depth_and_ticker_to_orderbook_state_basic():
    state = depth_and_ticker_to_orderbook_state(
        _depth(), _ticker(), depth_check_levels=5, received_ts_ms=2000
    )
    assert state.symbol == "BTCUSDT"
    assert state.best_bid == 100.0
    assert state.best_ask == 100.1
    assert state.bid_depth_5lvl_usd > 0
    assert state.ask_depth_5lvl_usd > 0


def test_orderbook_state_spread_bps():
    state = depth_and_ticker_to_orderbook_state(
        _depth(), _ticker(), depth_check_levels=5, received_ts_ms=2000
    )
    assert state.spread_bps == pytest.approx((100.1 - 100.0) / state.mid * 10_000)


def test_orderbook_state_not_crossed():
    state = depth_and_ticker_to_orderbook_state(
        _depth(), _ticker(), depth_check_levels=5, received_ts_ms=2000
    )
    assert state.is_crossed is False


def test_orderbook_state_crossed_book():
    ticker = RawBookTicker(
        symbol="BTCUSDT", best_bid=101.0, best_bid_qty=1.0, best_ask=100.0, best_ask_qty=1.0, event_time_ms=1000
    )
    state = depth_and_ticker_to_orderbook_state(
        _depth(), ticker, depth_check_levels=5, received_ts_ms=2000
    )
    assert state.is_crossed is True


def test_orderbook_state_empty_side_raises_at_construction():
    empty_depth = RawDepthSnapshot(symbol="BTCUSDT", bids=[], asks=[], event_time_ms=1000, last_update_id=1)
    with pytest.raises(DataIntegrityError):
        depth_and_ticker_to_orderbook_state(
            empty_depth, _ticker(), depth_check_levels=5, received_ts_ms=2000
        )


def test_depth_check_levels_must_be_positive():
    with pytest.raises(ValueError):
        depth_and_ticker_to_orderbook_state(
            _depth(), _ticker(), depth_check_levels=0, received_ts_ms=2000
        )


def test_depth_check_levels_limits_summed_levels():
    state_5 = depth_and_ticker_to_orderbook_state(
        _depth(n_levels=10), _ticker(), depth_check_levels=5, received_ts_ms=2000
    )
    state_10 = depth_and_ticker_to_orderbook_state(
        _depth(n_levels=10), _ticker(), depth_check_levels=10, received_ts_ms=2000
    )
    assert state_10.bid_depth_5lvl_usd > state_5.bid_depth_5lvl_usd
