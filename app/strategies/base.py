"""Base contract every strategy (S1-S5) implements.

A strategy is a pure function: `(MarketSnapshot, NewsState, StrategyConfig)
-> list[CandidateSignal]`. No I/O, no shared mutable state, no reading
of the wall clock (time comes from `snapshot.as_of_ts_ms`). Every
fail-closed path returns `[]`, never `None`, and never raises for an
*expected* missing-data condition — only a genuine bug should
propagate as an exception, and even then the veto engine converts it
into a BLOCK rather than crashing the evaluation loop (spec section 12).

Every strategy's `meta` dict on its CandidateSignal(s) MUST include at
minimum:
  - "atr14": the ATR(14) value used in the calculation
  - "snapshot_version": the MarketSnapshot's version string
  - "event_ts_ms": the timestamp of the bar/event the signal is based on
  - every named threshold constant actually used in that evaluation

This is enforced by `validate_meta_contract` below and is what makes
runtime consistency checks possible.

R CONVENTION (mandatory for every strategy, S1-S5)
---------------------------------------------------
R is always measured from the entry-zone MIDPOINT, never from
entry_low or entry_high alone:

    entry_mid = (entry_low + entry_high) / 2
    R         = |entry_mid - stop_loss|
    TP_k      = entry_mid +/- k * R   (sign per direction)

This must match how signal_engine.py computes post-cost R:R (it also
uses entry_mid), or a strategy's own "2R" target is not actually 2R as
graded — this gap (S1 originally measured R from entry_low/entry_high
while the engine graded from the midpoint) was found during Batch 2
review and is exactly the kind of bug this fresh build exists to catch
before it propagates into every strategy. S4 already used entry_mid
directly (with entry_low == entry_high, a zero-width zone); S1 has
been corrected to match. S2, S3, and S5 must use this convention from
first implementation, not retrofit it later.

Because R:R is graded from the midpoint, a wide entry zone makes "R"
ambiguous depending on where within the zone a user's limit order
actually fills — see `passes_max_entry_zone_width` below, enforced for
every strategy via `finalize_candidates`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.logging import get_logger
from app.core.models import CandidateSignal, MarketSnapshot, NewsState

logger = get_logger(__name__)

REQUIRED_META_KEYS = ("atr14", "snapshot_version", "event_ts_ms")


def passes_min_stop_cost_multiple(
    candidate: CandidateSignal,
    snapshot: MarketSnapshot,
    min_stop_cost_multiple: float,
) -> bool:
    """Minimum stop-distance filter (class E, engineering safety).

    Reject any candidate where

        stop_distance < min_stop_cost_multiple * round_trip_cost_at_TP

    "A stop distance below 3x the cost of entry+exit is not a tradable
    setup; costs eat the stop before price moves."

    This is not threshold tuning: it refuses to publish setups where the
    arithmetic cannot work. round_trip_cost_at_TP is 2 x maker fee
    (limit entry + limit take-profit), the same figure used for R:R at
    TP2 (see app/backtest/costs.py). Spread and slippage are not
    included because the resting limit legs do not cross the spread.

    stop_distance is measured from the entry-zone midpoint to the stop,
    matching how signal_engine computes R:R.

    Provenance note: this filter was added after an early Batch 2 demo
    fixture produced a ~5.5 bps stop (measured, not the ~3.2 bps
    initially estimated from memory) that the cost model correctly
    flagged as uneconomical. The fixture was unrealistic, not S1 itself
    (see KNOWN_UNCERTAINTIES.md discussion in the Batch 2/3 review), but
    the filter is a genuine, permanent safety net regardless of any one
    fixture's realism.
    """
    entry_mid = (candidate.entry_low + candidate.entry_high) / 2
    stop_distance = abs(entry_mid - candidate.stop_loss)
    round_trip_at_tp = 2.0 * entry_mid * snapshot.fee_maker_bps / 10_000
    return stop_distance >= min_stop_cost_multiple * round_trip_at_tp


def passes_max_entry_zone_width(
    candidate: CandidateSignal,
    max_width_r_multiple: float,
) -> bool:
    """Maximum entry-zone-width filter (class E, engineering safety).

    Reject any candidate where

        entry_zone_width > max_width_r_multiple * R

    where R is the midpoint-based R defined in this module's docstring
    (R CONVENTION section) and entry_zone_width = entry_high - entry_low.

    Rationale: R:R is graded from the entry-zone MIDPOINT. If the zone
    is wide relative to R, "R" is itself ambiguous for a user who fills
    anywhere else in the zone — a fill near one edge could see a
    meaningfully different realized R than the midpoint-graded figure
    implies. Capping width at max_width_r_multiple * R (default 0.30,
    i.e. 30%) keeps the R measurement meaningful across every fill
    location in the zone. This is engineering safety, not tuning: it
    does not change what a signal claims, it refuses to publish signals
    whose entry zone is too wide for that claim to be meaningful.
    """
    entry_mid = (candidate.entry_low + candidate.entry_high) / 2
    r = abs(entry_mid - candidate.stop_loss)
    if r <= 0:
        return False  # degenerate R, cannot evaluate width against it
    width = candidate.entry_high - candidate.entry_low
    return width <= max_width_r_multiple * r


class StrategyBase(ABC):
    """Abstract base class for S1-S5. Subclasses implement `evaluate`
    as a pure function and declare their `strategy_id`."""

    strategy_id: str

    def finalize_candidates(
        self,
        candidates: list[CandidateSignal],
        snapshot: MarketSnapshot,
        config: dict,
    ) -> list[CandidateSignal]:
        """Shared post-processing every strategy applies to its raw
        candidates before returning them: the minimum stop-distance
        filter AND the maximum entry-zone-width filter. Strategies call
        this on their way out of `evaluate` so neither filter can be
        forgotten by an individual strategy.

        Both multiples are read from config["common"] (class E each).
        A missing key is a config error, not a silent default.
        """
        stop_cost_multiple = config["common"]["min_stop_cost_multiple"]
        width_multiple = config["common"]["max_entry_zone_width_r_multiple"]
        out: list[CandidateSignal] = []
        for candidate in candidates:
            if not passes_min_stop_cost_multiple(candidate, snapshot, stop_cost_multiple):
                logger.info(
                    "candidate_filtered_finalize",
                    extra={"context": {
                        "symbol": candidate.symbol, "strategy": candidate.strategy_source,
                        "direction": candidate.direction.value, "confidence": candidate.confidence,
                        "reason": "min_stop_cost_multiple",
                    }},
                )
                continue
            if not passes_max_entry_zone_width(candidate, width_multiple):
                logger.info(
                    "candidate_filtered_finalize",
                    extra={"context": {
                        "symbol": candidate.symbol, "strategy": candidate.strategy_source,
                        "direction": candidate.direction.value, "confidence": candidate.confidence,
                        "reason": "max_entry_zone_width",
                    }},
                )
                continue
            out.append(candidate)
        return out

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        """Evaluate the strategy against one snapshot. Returns zero or
        more CandidateSignal objects. MUST return [] (never None, never
        raise) for any expected missing-data / fail-closed condition.

        This method has no body other than its docstring: `ABC` plus
        `@abstractmethod` already makes it impossible to instantiate
        any subclass that fails to override this, so no runtime guard
        is needed here — every concrete strategy (S1-S5) provides a
        complete implementation.
        """


def validate_meta_contract(candidate: CandidateSignal) -> list[str]:
    """Returns a list of missing required meta keys (empty if all
    present). Used by tests to assert the audit trail
    is complete before attempting recomputation."""
    return [k for k in REQUIRED_META_KEYS if k not in candidate.meta]
