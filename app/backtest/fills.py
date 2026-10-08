"""Fill modeling: limit-fill probability and partial-TP exit tracking,
for the backtest engine (spec section 22: "Limit-fill aware
(fill-probability model)", "Partial-TP aware").

FILL PROBABILITY MODEL (documented assumption, class E — not
empirically calibrated, consistent with KNOWN_UNCERTAINTIES.md item 6
on cost-model assumptions in general): a signal's entry is a resting
limit order inside [entry_low, entry_high]. Given one bar's OHLC, the
order is considered FILLED if the bar's range touches the entry zone
at all (low <= entry_high AND high >= entry_low — standard range-
overlap test), and the fill PROBABILITY within that bar is modeled as
the fraction of the entry zone's width that the bar's range actually
covers, capped at 1.0. This is a simplification (real fill probability
depends on order-book depth, queue position, and intrabar path, none
of which OHLC bars capture) — it is deliberately conservative-leaning
and documented as an assumption, not a measurement.

PARTIAL-TP MODEL: exits the position in the configured fractions
(spec section 21 / config/risk.yaml's partial_exit_fractions, default
40/30/20/10) as each TP level is reached, in order. A trade that never
reaches TP1 and instead hits the stop loses the full remaining
position at the stop price. This module tracks exactly how much of the
original position remains open after each TP/SL event — the backtest
engine (engine.py) is responsible for walking bar-by-bar and calling
into this module's pure functions at each relevant event.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.math import OHLC
from app.core.models import Direction


@dataclass(frozen=True)
class FillAssessment:
    is_filled: bool
    fill_probability: float
    fill_price: float | None


def assess_limit_fill(
    *, entry_low: float, entry_high: float, bar: OHLC, direction: Direction
) -> FillAssessment:
    """Assess whether a resting limit order inside [entry_low,
    entry_high] would fill against `bar`'s OHLC range, and the modeled
    fill probability.

    Fill price convention: the more conservative (worse-for-the-trader)
    boundary of the overlap between the bar's range and the entry
    zone — for LONG, the higher of entry_low and bar.low (a buy limit
    fills at the best available price at or below where it rests, but
    we never assume better than the entry zone's own boundary); for
    SHORT, the lower of entry_high and bar.high. This avoids ever
    crediting the backtest with an unrealistically favorable fill.
    """
    if entry_low > entry_high:
        raise ValueError(f"entry_low ({entry_low}) > entry_high ({entry_high})")

    overlap_low = max(entry_low, bar.low)
    overlap_high = min(entry_high, bar.high)

    if overlap_low > overlap_high:
        return FillAssessment(is_filled=False, fill_probability=0.0, fill_price=None)

    entry_width = entry_high - entry_low
    if entry_width <= 0:
        # Degenerate zero-width zone (e.g. S2/S3/S5's single-price
        # entries): filled iff the bar's range touches that exact
        # price, probability is binary.
        touched = bar.low <= entry_low <= bar.high
        fill_price = entry_low if touched else None
        return FillAssessment(is_filled=touched, fill_probability=1.0 if touched else 0.0, fill_price=fill_price)

    overlap_width = overlap_high - overlap_low
    probability = min(1.0, overlap_width / entry_width)

    if direction == Direction.LONG:
        fill_price = max(entry_low, bar.low)
    else:
        fill_price = min(entry_high, bar.high)

    return FillAssessment(is_filled=True, fill_probability=probability, fill_price=fill_price)


@dataclass(frozen=True)
class PartialExitEvent:
    tp_index: int  # 0..3 for TP1..TP4
    price: float
    fraction_of_original: float
    bar_close_time_ms: int


@dataclass
class PositionState:
    """Mutable tracker for one open position's remaining size across
    partial TP exits. `remaining_fraction` starts at 1.0 (full
    position) and decreases as each TP level is hit, in order."""

    direction: Direction
    entry_price: float
    stop_loss: float
    tp_levels: tuple[float, float, float, float]
    partial_exit_fractions: tuple[float, float, float, float]
    remaining_fraction: float = 1.0
    next_tp_index: int = 0
    exits: list[PartialExitEvent] = None  # type: ignore[assignment]
    stopped_out: bool = False
    stop_out_bar_close_time_ms: int | None = None

    def __post_init__(self) -> None:
        if self.exits is None:
            self.exits = []
        total = sum(self.partial_exit_fractions)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"partial_exit_fractions must sum to 1.0, got {total}")

    @property
    def is_fully_closed(self) -> bool:
        return self.remaining_fraction <= 1e-9 or self.stopped_out


def _tp_hit(direction: Direction, tp_price: float, bar: OHLC) -> bool:
    if direction == Direction.LONG:
        return bar.high >= tp_price
    return bar.low <= tp_price


def _sl_hit(direction: Direction, stop_loss: float, bar: OHLC) -> bool:
    if direction == Direction.LONG:
        return bar.low <= stop_loss
    return bar.high >= stop_loss


def process_bar_for_open_position(position: PositionState, bar: OHLC) -> PositionState:
    """Advance `position`'s state by one bar: checks for stop-loss hit
    (closes the remaining position entirely) and for the NEXT pending
    TP level being hit (partial exit of its configured fraction).

    NEXT-BAR-FILL DISCIPLINE: this function must only ever be called
    with a bar that is chronologically AFTER the signal's entry fill
    bar (the backtest engine, per spec section 22's "next-bar fill
    only", never evaluates exit conditions on the same bar an order
    was filled on) — enforced by the engine's bar-walking order, not
    re-checked here (this function has no notion of "which bar is the
    entry bar", by design, so it stays a pure, reusable per-bar step).

    Within a single bar, if BOTH the stop and the next TP level would
    be hit (a wide bar), the STOP takes precedence — conservative,
    worst-case ordering (we do not assume the favorable TP was reached
    first within the bar, since OHLC data cannot tell us the intrabar
    path).
    """
    if position.is_fully_closed:
        return position

    if _sl_hit(position.direction, position.stop_loss, bar):
        position.stopped_out = True
        position.stop_out_bar_close_time_ms = bar.close_time_ms
        return position

    if position.next_tp_index < len(position.tp_levels):
        tp_price = position.tp_levels[position.next_tp_index]
        if _tp_hit(position.direction, tp_price, bar):
            fraction = position.partial_exit_fractions[position.next_tp_index]
            position.exits.append(
                PartialExitEvent(
                    tp_index=position.next_tp_index, price=tp_price,
                    fraction_of_original=fraction, bar_close_time_ms=bar.close_time_ms,
                )
            )
            position.remaining_fraction -= fraction
            position.next_tp_index += 1

    return position
