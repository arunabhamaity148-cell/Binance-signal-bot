"""Signal payload model.

Implements SIGNAL_MODEL.md exactly: a frozen pydantic model with every
construction-time invariant enforced as a validator. A Signal that
fails validation raises at construction time and is treated as a
data-consistency failure — never silently coerced into a
"close enough" valid signal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.math import is_tick_aligned, round_down_to_step

ADVISORY_WARNING = (
    "ADVISORY ONLY — VERIFY ACCOUNT SIZING MANUALLY. No account state, "
    "fill, leverage, or liquidation distance is observed.\n"
    "LEVERAGE NOT SET BY BOT. Set leverage yourself on the exchange."
)


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_id: str
    created_ts_ms: int
    symbol: str
    direction: Literal["LONG", "SHORT"]
    grade: Literal["A+", "A", "B"]
    confidence: float
    strategy_source: Literal["S1", "S2", "S3", "S4", "S5"]

    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    rr_tp2: float

    expiry_ts_ms: int

    why_lines: list[str]
    veto_state: Literal["PASS", "BLOCK"]
    veto_reason: str | None = None

    advisory_warning: str = ADVISORY_WARNING

    size_units_advisory: float
    notional_usd_advisory: float
    sizing_multiplier: float = 1.0
    regime: str = "UNKNOWN"
    htf_confluence: bool = False

    meta: dict

    # Optional semi-automated Delta execution values. These never imply
    # order placement; false/None means the public product metadata was not
    # available for this signal.
    delta_available: bool = False
    delta_symbol: str | None = None
    delta_contracts: float | None = None
    delta_entry_low: float | None = None
    delta_entry_high: float | None = None
    delta_stop_loss: float | None = None
    delta_tp1: float | None = None
    delta_tp2: float | None = None
    delta_tp3: float | None = None
    delta_tp4: float | None = None
    delta_rr_tp2: float | None = None

    # These are supplied at validation time via a private context
    # mechanism (see `build_signal` below) rather than being pydantic
    # fields themselves, since price_tick/qty_step are properties of
    # the symbol's exchange filters, not of the signal.
    _price_tick: float | None = None
    _qty_step: float | None = None

    @model_validator(mode="after")
    def _validate_directional_ordering(self) -> "Signal":
        if self.entry_low > self.entry_high:
            raise ValueError(f"entry_low ({self.entry_low}) > entry_high ({self.entry_high})")

        if self.direction == "LONG":
            if not (
                self.stop_loss < self.entry_low
                and self.entry_high < self.tp1 < self.tp2 < self.tp3 < self.tp4
            ):
                raise ValueError(
                    "LONG signal violates required ordering "
                    "stop_loss < entry_low <= entry_high < tp1 < tp2 < tp3 < tp4: "
                    f"sl={self.stop_loss} el={self.entry_low} eh={self.entry_high} "
                    f"tp1={self.tp1} tp2={self.tp2} tp3={self.tp3} tp4={self.tp4}"
                )
        else:  # SHORT
            if not (
                self.tp4 < self.tp3 < self.tp2 < self.tp1 < self.entry_low
                and self.entry_high < self.stop_loss
            ):
                raise ValueError(
                    "SHORT signal violates required ordering "
                    "tp4 < tp3 < tp2 < tp1 < entry_low <= entry_high < stop_loss: "
                    f"sl={self.stop_loss} el={self.entry_low} eh={self.entry_high} "
                    f"tp1={self.tp1} tp2={self.tp2} tp3={self.tp3} tp4={self.tp4}"
                )
        return self

    @model_validator(mode="after")
    def _validate_positive_prices(self) -> "Signal":
        prices = [
            self.entry_low, self.entry_high, self.stop_loss,
            self.tp1, self.tp2, self.tp3, self.tp4,
        ]
        if any(p <= 0 for p in prices):
            raise ValueError(f"all prices must be > 0, got {prices}")
        return self

    @model_validator(mode="after")
    def _validate_confidence_range(self) -> "Signal":
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        return self

    @model_validator(mode="after")
    def _validate_why_lines(self) -> "Signal":
        if len(self.why_lines) > 10:
            raise ValueError(f"why_lines must have <= 10 lines, got {len(self.why_lines)}")
        return self

    @model_validator(mode="after")
    def _validate_veto_reason_consistency(self) -> "Signal":
        if self.veto_state == "BLOCK" and not self.veto_reason:
            raise ValueError("veto_state is BLOCK but veto_reason is empty")
        if self.veto_state == "PASS" and self.veto_reason:
            raise ValueError("veto_state is PASS but veto_reason is set")
        return self

    @model_validator(mode="after")
    def _validate_size_non_negative(self) -> "Signal":
        if self.size_units_advisory < 0:
            raise ValueError(f"size_units_advisory must be >= 0, got {self.size_units_advisory}")
        if self.notional_usd_advisory < 0:
            raise ValueError(f"notional_usd_advisory must be >= 0, got {self.notional_usd_advisory}")
        return self

    @model_validator(mode="after")
    def _validate_phase1_metadata(self) -> "Signal":
        if not (0.25 <= self.sizing_multiplier <= 1.0):
            raise ValueError("sizing_multiplier must be in [0.25, 1.0]")
        if self.regime not in {"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}:
            raise ValueError(f"unknown market regime: {self.regime}")
        return self


def build_signal(
    *,
    signal_id: str,
    created_ts_ms: int,
    symbol: str,
    direction: str,
    grade: str,
    confidence: float,
    strategy_source: str,
    entry_low: float,
    entry_high: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    tp3: float,
    tp4: float,
    rr_tp2: float,
    expiry_ts_ms: int,
    why_lines: list[str],
    veto_state: str,
    veto_reason: str | None,
    size_units_advisory: float,
    notional_usd_advisory: float,
    meta: dict,
    price_tick: float,
    qty_step: float,
    sizing_multiplier: float = 1.0,
    regime: str = "UNKNOWN",
    htf_confluence: bool = False,
) -> Signal:
    """Construct a Signal, additionally validating price-tick and
    qty-step alignment (which require the symbol's exchange filters,
    not available to a pure pydantic field validator without external
    context). Raises pydantic.ValidationError or ValueError on any
    violation.
    """
    for label, price in (
        ("entry_low", entry_low), ("entry_high", entry_high), ("stop_loss", stop_loss),
        ("tp1", tp1), ("tp2", tp2), ("tp3", tp3), ("tp4", tp4),
    ):
        if not is_tick_aligned(price, price_tick):
            raise ValueError(
                f"{label}={price} is not aligned to price_tick={price_tick} for {symbol}"
            )

    aligned_size = round_down_to_step(size_units_advisory, qty_step)

    return Signal(
        signal_id=signal_id,
        created_ts_ms=created_ts_ms,
        symbol=symbol,
        direction=direction,
        grade=grade,
        confidence=confidence,
        strategy_source=strategy_source,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        tp4=tp4,
        rr_tp2=rr_tp2,
        expiry_ts_ms=expiry_ts_ms,
        why_lines=why_lines,
        veto_state=veto_state,
        veto_reason=veto_reason,
        size_units_advisory=aligned_size,
        notional_usd_advisory=notional_usd_advisory,
        sizing_multiplier=sizing_multiplier,
        regime=regime,
        htf_confluence=htf_confluence,
        meta=meta,
    )
