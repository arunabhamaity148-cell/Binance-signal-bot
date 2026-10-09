"""Order book state construction.

For this bot's purposes (spread and top-of-book depth for the active
spread guard), we don't need to maintain a fully diffed, incrementally
updated local order book — the `depth20@100ms` stream already delivers
a top-20 snapshot on every update, which is sufficient. This module
converts that snapshot plus the latest bookTicker into an
`OrderBookState`.
"""

from __future__ import annotations

from app.core.errors import DataIntegrityError
from app.core.models import OrderBookState
from app.data.binance.models import RawBookTicker, RawDepthSnapshot


def depth_and_ticker_to_orderbook_state(
    depth: RawDepthSnapshot,
    ticker: RawBookTicker,
    *,
    depth_check_levels: int,
    received_ts_ms: int,
) -> OrderBookState:
    """Build an OrderBookState from a depth20 snapshot and the latest
    bookTicker. Uses bookTicker for best bid/ask (lowest latency,
    dedicated stream) and the depth snapshot for cumulative depth
    within `depth_check_levels` levels on each side.
    """
    if depth_check_levels <= 0:
        raise ValueError("depth_check_levels must be positive")
    if not depth.bids or not depth.asks:
        raise DataIntegrityError(f"{depth.symbol}: depth snapshot has an empty side")

    bid_levels = depth.bids[:depth_check_levels]
    ask_levels = depth.asks[:depth_check_levels]

    bid_depth_usd = sum(lvl.price * lvl.quantity for lvl in bid_levels)
    ask_depth_usd = sum(lvl.price * lvl.quantity for lvl in ask_levels)

    event_ts_ms = max(depth.event_time_ms, ticker.event_time_ms)

    return OrderBookState(
        symbol=depth.symbol,
        best_bid=ticker.best_bid,
        best_ask=ticker.best_ask,
        bid_depth_5lvl_usd=bid_depth_usd,
        ask_depth_5lvl_usd=ask_depth_usd,
        event_ts_ms=event_ts_ms,
        received_ts_ms=received_ts_ms,
    )
