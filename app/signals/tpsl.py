"""TP/SL helpers used by the signal engine.

All fee/cost arithmetic lives in app/backtest/costs.py, which is the
single source of truth shared with the backtest engine. This module
adapts a MarketSnapshot to that model and keeps the price-tick helper.
See costs.py for why each leg has the fee class it has.
"""

from __future__ import annotations

from app.backtest.costs import (
    CostBreakdown,
    cash_risk_per_unit,
    compute_cost_breakdown,
    compute_rr_at_tp,
    estimate_slippage_bps,
)
from app.core.math import round_to_tick
from app.core.models import MarketSnapshot

__all__ = [
    "CostBreakdown",
    "align_prices_to_tick",
    "cash_risk_per_unit",
    "compute_rr_at_tp",
    "estimate_cost",
    "estimate_slippage_bps",
]


def estimate_cost(
    *,
    entry_price: float,
    stop_price: float,
    snapshot: MarketSnapshot,
    notional_usd: float,
) -> CostBreakdown:
    """Build a CostBreakdown for one signal from the snapshot's fee
    schedule and current book depth (if available)."""
    depth_usd = None
    if snapshot.orderbook is not None:
        depth_usd = min(
            snapshot.orderbook.bid_depth_5lvl_usd, snapshot.orderbook.ask_depth_5lvl_usd
        )
    return compute_cost_breakdown(
        entry_price=entry_price,
        stop_price=stop_price,
        fee_maker_bps=snapshot.fee_maker_bps,
        fee_taker_bps=snapshot.fee_taker_bps,
        notional_usd=notional_usd,
        depth_usd=depth_usd,
    )


def align_prices_to_tick(prices: dict[str, float], price_tick: float) -> dict[str, float]:
    """Round every price in `prices` to the symbol's price_tick."""
    return {k: round_to_tick(v, price_tick) for k, v in prices.items()}
