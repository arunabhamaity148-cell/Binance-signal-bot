"""Snapshot assembly.

Builds the single, immutable, versioned MarketSnapshot per symbol per
evaluation tick. Every strategy and every guard reads from the SAME
instance for that tick — nothing re-fetches live data mid-evaluation,
which is what makes strategies and guards pure, deterministic
functions and what makes guard G12 (self-consistency re-derivation)
meaningful.

HYBRID OPEN-INTEREST MERGE HAPPENS HERE
-----------------------------------------
Per the hybrid OI design in app/data/derivatives.py, the "current" OI
point every strategy and guard reads (the last element of
`derivatives.open_interest_history_5m`) should be live-fresh, not
bucket-fresh. That merge (`merge_live_oi_into_5m_series`) is performed
during snapshot assembly, not by the orchestration layer (main.py/
bot.py) and not by individual strategies: strategies consume immutable
snapshots and must never assemble or re-fetch data themselves, and the
orchestration layer's job is triggering evaluation, not data assembly.
Snapshot assembly is the one place both concerns are already met.

Fail-closed behavior: if the supplied live OI point is itself stale
(older than `oi_stale_ms` relative to `as_of_ts_ms`), it is NOT merged
in — merging a stale point as though it were the fresh "current" value
would defeat the entire purpose of the hybrid design (a stale value
masquerading as fresh is worse than an honestly-stale bucketed value,
since every downstream staleness check trusts the merge already
happened correctly). In that case the series is left as the hist-only
series, and downstream staleness checks (G5, G13, G14, and each
strategy's own oi_stale_ms check) will correctly see and fail closed
on however stale the newest surviving point actually is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import SnapshotIncompleteError
from app.core.models import (
    DerivativesState,
    FeedHealth,
    MarketSnapshot,
    OrderBookState,
    SymbolKlines,
    TakerFlowState,
    TimestampedValue,
)
from app.core.time_utils import is_stale
from app.data.derivatives import merge_live_oi_into_5m_series

REQUIRED_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


@dataclass
class SnapshotInputs:
    """Everything needed to assemble one symbol's snapshot for one
    tick. Populated by the main evaluation loop from the data layer's
    current state (klines cache, latest orderbook, latest taker flow,
    latest derivatives, feed health map).

    `live_oi_point`, if supplied, is the most recent
    /fapi/v1/openInterest poll result for this symbol (see
    app/data/derivatives.py's hybrid OI design). It is merged into
    `derivatives.open_interest_history_5m` during assembly, subject to
    the `oi_stale_ms` freshness check below. Callers that have no live
    poll result yet (e.g. immediately after boot, before the first poll
    completes) pass None, and assembly proceeds using the hist-only
    series unchanged — this is itself a fail-closed-friendly state,
    since downstream staleness checks will see however old the
    hist-only series' newest point actually is.
    """

    symbol: str
    as_of_ts_ms: int
    klines: dict[str, SymbolKlines]
    orderbook: OrderBookState | None
    taker_flow: TakerFlowState | None
    derivatives: DerivativesState | None
    feed_health: dict[str, FeedHealth]
    price_tick: float
    qty_step: float
    min_qty: float
    fee_maker_bps: float
    fee_taker_bps: float
    live_oi_point: TimestampedValue | None = None
    oi_stale_ms: int | None = None


def build_snapshot(inputs: SnapshotInputs, *, min_candles: int) -> MarketSnapshot:
    """Construct a MarketSnapshot, enforcing the minimum-history
    requirement referenced throughout the strategy specs
    (min_candles, default 60, on the primary 5m timeframe).

    Raises SnapshotIncompleteError if the 5m series has fewer than
    `min_candles` closed bars. Other timeframes are allowed to be
    thinner (strategies check their own specific requirements), but 5m
    is universally required since every strategy uses it directly or
    indirectly for ATR.
    """
    five_min = inputs.klines.get("5m")
    if five_min is None or len(five_min.bars) < min_candles:
        have = 0 if five_min is None else len(five_min.bars)
        raise SnapshotIncompleteError(
            f"{inputs.symbol}: insufficient 5m history for snapshot "
            f"({have} < {min_candles} required)"
        )

    version = f"{inputs.symbol}-{inputs.as_of_ts_ms}-{uuid.uuid4().hex[:8]}"

    derivatives = _apply_live_oi_merge(inputs)

    return MarketSnapshot(
        snapshot_version=version,
        symbol=inputs.symbol,
        as_of_ts_ms=inputs.as_of_ts_ms,
        klines=inputs.klines,
        orderbook=inputs.orderbook,
        taker_flow=inputs.taker_flow,
        derivatives=derivatives,
        feed_health=inputs.feed_health,
        price_tick=inputs.price_tick,
        qty_step=inputs.qty_step,
        min_qty=inputs.min_qty,
        fee_maker_bps=inputs.fee_maker_bps,
        fee_taker_bps=inputs.fee_taker_bps,
    )


def _apply_live_oi_merge(inputs: SnapshotInputs) -> DerivativesState | None:
    """Merge `inputs.live_oi_point` into `inputs.derivatives`'
    open_interest_history_5m, subject to the staleness fail-closed rule
    documented at module level. Returns `inputs.derivatives` unchanged
    if there is nothing to merge (no derivatives state, no live point,
    or the live point is stale).
    """
    if inputs.derivatives is None or inputs.live_oi_point is None:
        return inputs.derivatives

    if inputs.oi_stale_ms is None:
        # No staleness budget was supplied alongside a live point: this
        # is a caller-configuration error, not a market-data condition
        # — fail closed by refusing to merge rather than guessing a
        # budget, exactly as strategies fail closed on missing config.
        return inputs.derivatives

    if is_stale(
        event_ts_ms=inputs.live_oi_point.event_ts_ms,
        received_ts_ms=inputs.live_oi_point.received_ts_ms,
        as_of_ts_ms=inputs.as_of_ts_ms,
        staleness_budget_ms=inputs.oi_stale_ms,
    ):
        return inputs.derivatives  # stale live point: do NOT merge it in

    merged_5m = merge_live_oi_into_5m_series(
        inputs.derivatives.open_interest_history_5m, inputs.live_oi_point
    )

    return DerivativesState(
        symbol=inputs.derivatives.symbol,
        funding_rate_history=inputs.derivatives.funding_rate_history,
        open_interest_history_5m=merged_5m,
        open_interest_history_15m=inputs.derivatives.open_interest_history_15m,
        open_interest_history_1h=inputs.derivatives.open_interest_history_1h,
        open_interest_history_1d=inputs.derivatives.open_interest_history_1d,
        long_short_account_ratio_history=inputs.derivatives.long_short_account_ratio_history,
        taker_long_short_ratio_history=inputs.derivatives.taker_long_short_ratio_history,
        premium_index_current=inputs.derivatives.premium_index_current,
    )
