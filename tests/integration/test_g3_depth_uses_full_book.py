from __future__ import annotations

import pytest

from app.config import load_all
from app.data.binance.models import RawBookTicker, RawDepthLevel, RawDepthSnapshot
from app.data.orderbook import depth_and_ticker_to_orderbook_state
from app.risk import veto
from tests.veto.test_veto_guards import _base_snapshot, _empty_news


VETO_CFG = load_all().veto


def _depth_20() -> RawDepthSnapshot:
    return RawDepthSnapshot(
        symbol="OPUSDT",
        bids=[RawDepthLevel(price=100.0 - i, quantity=float(i + 1)) for i in range(20)],
        asks=[RawDepthLevel(price=100.2 + i, quantity=float(i + 1)) for i in range(20)],
        event_time_ms=10_000_000,
        last_update_id=1,
    )


def _ticker() -> RawBookTicker:
    return RawBookTicker(
        symbol="OPUSDT", best_bid=100.0, best_bid_qty=1.0,
        best_ask=100.2, best_ask_qty=1.0, event_time_ms=10_000_000,
    )


def test_g3_depth_uses_all_configured_levels_per_side():
    depth = _depth_20()
    state = depth_and_ticker_to_orderbook_state(
        depth, _ticker(),
        depth_check_levels=VETO_CFG["g3_depth_collapse"]["depth_check_levels"],
        received_ts_ms=10_000_000,
    )

    expected_bid = sum(level.price * level.quantity for level in depth.bids[:20])
    expected_ask = sum(level.price * level.quantity for level in depth.asks[:20])
    assert state.bid_depth_5lvl_usd == pytest.approx(expected_bid)
    assert state.ask_depth_5lvl_usd == pytest.approx(expected_ask)


def test_g3_depth_uses_the_smaller_side():
    state = depth_and_ticker_to_orderbook_state(
        _depth_20(), _ticker(), depth_check_levels=20, received_ts_ms=10_000_000,
    )
    snapshot = _base_snapshot(
        symbol="OPUSDT", orderbook=state, as_of_ts_ms=10_000_000,
    )

    result = veto.guard_g3_depth_collapse(
        snapshot, _empty_news(), None,
        VETO_CFG["g3_depth_collapse"], symbol_tier="small_caps",
    )
    assert not result.passed
    assert result.reason.startswith("depth 18340 USD below minimum 30000 USD")
