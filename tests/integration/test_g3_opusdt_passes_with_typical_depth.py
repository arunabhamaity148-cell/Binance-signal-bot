from __future__ import annotations

from app.config import load_all
from app.data.binance.models import RawBookTicker, RawDepthLevel, RawDepthSnapshot
from app.data.orderbook import depth_and_ticker_to_orderbook_state
from app.risk import veto
from tests.veto.test_veto_guards import _base_snapshot, _empty_news


VETO_CFG = load_all().veto


def test_opusdt_typical_full_book_depth_passes_g3():
    # The first few OPUSDT levels can be thin, while cumulative depth across
    # Binance's depth20 snapshot remains above the unchanged 30,000 USD floor.
    bids = [RawDepthLevel(price=0.12575 - i * 0.00001, quantity=1000.0 + i * 1500.0) for i in range(20)]
    asks = [RawDepthLevel(price=0.12577 + i * 0.00001, quantity=1000.0 + i * 1600.0) for i in range(20)]
    depth = RawDepthSnapshot(
        symbol="OPUSDT", bids=bids, asks=asks,
        event_time_ms=10_000_000, last_update_id=1,
    )
    ticker = RawBookTicker(
        symbol="OPUSDT", best_bid=bids[0].price, best_bid_qty=bids[0].quantity,
        best_ask=asks[0].price, best_ask_qty=asks[0].quantity,
        event_time_ms=10_000_000,
    )
    state = depth_and_ticker_to_orderbook_state(
        depth, ticker,
        depth_check_levels=VETO_CFG["g3_depth_collapse"]["depth_check_levels"],
        received_ts_ms=10_000_000,
    )
    snapshot = _base_snapshot(
        symbol="OPUSDT", orderbook=state, as_of_ts_ms=10_000_000,
    )

    assert min(state.bid_depth_5lvl_usd, state.ask_depth_5lvl_usd) >= 30_000
    result = veto.guard_g3_depth_collapse(
        snapshot, _empty_news(), None,
        VETO_CFG["g3_depth_collapse"], symbol_tier="small_caps",
    )
    assert result.passed
