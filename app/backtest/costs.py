"""Cost model: single source of truth for fees, used by the live signal
path (signals/signal_engine.py, via signals/tpsl.py) and by the
backtest engine.

WHY EACH LEG HAS THE FEE CLASS IT HAS (signal-only architecture)
----------------------------------------------------------------
This bot never places orders. It publishes a signal that says
"LIMIT ENTRY", plus resting take-profit levels and a stop. A human
following that signal therefore pays:

  ENTRY  -> MAKER fee. The signal instructs a limit order inside the
            entry zone. A limit order that rests in the book and is
            filled by an incoming order is a maker fill.
  TP EXIT -> MAKER fee. Take-profits are limit orders resting at the
            TP prices. Same reasoning as entry.
  SL EXIT -> TAKER fee. A stop is a stop-market order: when the
            trigger price is hit it crosses the book and takes
            liquidity. It is the only leg that is a taker fill.

Assuming taker on every leg (the previous model) overstated the cost of
the two limit legs and made post-cost R:R look worse than what a user
following the signal would actually pay.

TWO DISTINCT ROUND-TRIP COSTS
-----------------------------
Which cost applies depends on the question being asked, because the
question determines which exit actually happens:

  1. R:R AT TP2  (what risk.yaml `min_rr_tp2` checks)
        round_trip = maker (entry) + maker (TP exit) = 2 x maker_bps
     The reward being evaluated is the take-profit scenario, so the
     exit is the TP limit order.

  2. POSITION-SIZING RISK  (what the user actually loses if stopped out)
        round_trip = maker (entry) + taker (SL exit) = maker_bps + taker_bps
     Risk is the stop-out scenario, so the exit is the stop-market
     order. Sizing on this figure means the cash lost at the stop,
     fees included, does not exceed the R budget.

Spread and slippage: a resting limit order does not cross the spread, so
neither is charged on the maker legs. The stop-market leg does cross the
book; its slippage is modelled separately (`sl_leg_slippage_per_unit`)
and is used in the sizing-risk figure only, never in the R:R-at-TP2
figure, so `min_rr_tp2` compares like with like as specified.

Fees are per-unit-of-base-asset in quote currency: price * bps / 10_000.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import Direction


@dataclass(frozen=True)
class CostBreakdown:
    """Per-unit costs, quote currency (USDT) per 1 unit of base asset.

    `entry_latency_slippage` is backtest-only (always 0.0 on the live
    path): it models the price drift between signal GENERATION and the
    moment a human actually places the limit order it describes. The
    live path has no such gap to model — signal_engine.py builds a
    signal from the current snapshot and the cost model is evaluated
    against that same snapshot, so there is no elapsed time for price
    to have moved. A backtest replaying historical bars, by contrast,
    must account for this gap explicitly (see
    estimate_latency_slippage_bps below) or it silently assumes
    instant, costless human reaction time — exactly the kind of
    look-ahead-adjacent optimism spec section 22 requires the backtest
    to avoid ("latency-aware").
    """

    entry_maker_fee: float
    tp_maker_fee: float
    sl_taker_fee: float
    sl_leg_slippage: float
    entry_latency_slippage: float = 0.0

    @property
    def round_trip_at_tp(self) -> float:
        """Entry (maker) + TP exit (maker) = 2 x maker, plus any entry
        latency slippage (backtest only; 0.0 on the live path). Used
        for R:R at TP2."""
        return self.entry_maker_fee + self.tp_maker_fee + self.entry_latency_slippage

    @property
    def round_trip_at_sl(self) -> float:
        """Entry (maker) + SL exit (taker) + stop-market slippage, plus
        any entry latency slippage. Used for position-sizing risk (what
        the user actually loses)."""
        return self.entry_maker_fee + self.sl_taker_fee + self.sl_leg_slippage + self.entry_latency_slippage


def estimate_slippage_bps(notional_usd: float, depth_usd: float | None) -> float:
    """Linear market-impact model. Explicit, documented modelling choice
    (KNOWN_UNCERTAINTIES.md item 6): NOT empirically calibrated.

    1 bp per 10% of available depth consumed, capped at 50 bps. Unknown
    depth falls back to a conservative 5 bps (class E)."""
    if depth_usd is None or depth_usd <= 0:
        return 5.0
    ratio = notional_usd / depth_usd
    return min(50.0, ratio * 10.0)


def estimate_latency_slippage_bps(latency_s: float, *, bps_per_second: float = 0.5) -> float:
    """Backtest-only latency-impact model (class E, engineering
    assumption, NOT empirically calibrated — consistent with every
    other cost-model assumption in this codebase): price impact grows
    linearly with the assumed human-reaction latency, at a fixed rate
    of `bps_per_second` (default 0.5 bps/s), capped at 25 bps (beyond
    which the entry zone itself would typically have been missed
    entirely — see fills.py's fill-probability model, which handles
    that case separately rather than via unbounded slippage growth
    here).

    `latency_s` must be >= 0. A latency of 0 (used by default on paths
    that don't model it) yields 0 slippage, matching the live path's
    implicit zero-latency assumption exactly.
    """
    if latency_s < 0:
        raise ValueError(f"latency_s must be >= 0, got {latency_s}")
    return min(25.0, latency_s * bps_per_second)


def compute_cost_breakdown(
    *,
    entry_price: float,
    stop_price: float,
    fee_maker_bps: float,
    fee_taker_bps: float,
    notional_usd: float,
    depth_usd: float | None,
    latency_s: float = 0.0,
) -> CostBreakdown:
    """Build the per-unit cost breakdown for one signal.

    The maker fee is applied at the entry price for both the entry and
    TP legs (TP price differs slightly from entry, but the difference is
    second-order and using entry price keeps R:R independent of which
    TP is evaluated). The taker fee and slippage are applied at the stop
    price, since that is where the stop-market order executes.

    `latency_s` defaults to 0.0 (the live path's implicit assumption —
    see CostBreakdown's docstring); the backtest engine passes a
    non-zero value per its configured assumed human-reaction delay.
    """
    if entry_price <= 0 or stop_price <= 0:
        raise ValueError("entry_price and stop_price must be > 0")
    if fee_maker_bps < 0 or fee_taker_bps < 0:
        raise ValueError("fee bps must be >= 0")

    entry_maker = entry_price * fee_maker_bps / 10_000
    tp_maker = entry_price * fee_maker_bps / 10_000
    sl_taker = stop_price * fee_taker_bps / 10_000
    slippage = stop_price * estimate_slippage_bps(notional_usd, depth_usd) / 10_000
    latency_slippage = entry_price * estimate_latency_slippage_bps(latency_s) / 10_000

    return CostBreakdown(
        entry_maker_fee=entry_maker,
        tp_maker_fee=tp_maker,
        sl_taker_fee=sl_taker,
        sl_leg_slippage=slippage,
        entry_latency_slippage=latency_slippage,
    )


def compute_rr_at_tp(
    *,
    direction: Direction,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    cost: CostBreakdown,
) -> float:
    """Post-cost R:R at a take-profit level, using round_trip_at_tp
    (2 x maker). This is the figure `min_rr_tp2` is checked against."""
    if direction == Direction.LONG:
        raw_risk = entry_price - stop_loss
        raw_reward = take_profit - entry_price
    else:
        raw_risk = stop_loss - entry_price
        raw_reward = entry_price - take_profit

    if raw_risk <= 0:
        raise ValueError(f"non-positive raw risk: {raw_risk}")

    round_trip = cost.round_trip_at_tp
    effective_risk = raw_risk + round_trip
    effective_reward = raw_reward - round_trip
    return effective_reward / effective_risk


def cash_risk_per_unit(
    *,
    direction: Direction,
    entry_price: float,
    stop_loss: float,
    cost: CostBreakdown,
) -> float:
    """Cash the user loses per unit if stopped out: raw stop distance
    plus round_trip_at_sl (maker entry + taker stop exit + stop-market
    slippage). This, not the raw stop distance, is the denominator for
    position sizing so the fee-inclusive loss stays within the R budget."""
    if direction == Direction.LONG:
        raw_risk = entry_price - stop_loss
    else:
        raw_risk = stop_loss - entry_price
    if raw_risk <= 0:
        raise ValueError(f"non-positive raw risk: {raw_risk}")
    return raw_risk + cost.round_trip_at_sl
