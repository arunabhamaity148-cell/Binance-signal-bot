"""Pure conversion of Binance-format advisory signals to Delta fields.

This module deliberately performs no I/O, authentication, or order activity.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _as_float(value: Decimal) -> float:
    return float(value)


def binance_qty_to_delta_contracts(
    binance_qty: float,
    contract_value: float,
    min_size: float,
) -> float:
    """Convert base-asset quantity to contracts, rounded down to min_size."""
    if binance_qty <= 0 or contract_value <= 0 or min_size <= 0:
        return 0.0
    contracts = _decimal(binance_qty) / _decimal(contract_value)
    multiple = (contracts / _decimal(min_size)).to_integral_value(rounding=ROUND_DOWN)
    result = multiple * _decimal(min_size)
    return _as_float(result) if result >= _decimal(min_size) else 0.0


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest tick using round-half-up."""
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    ticks = (_decimal(price) / _decimal(tick_size)).to_integral_value(rounding=ROUND_HALF_UP)
    return _as_float(ticks * _decimal(tick_size))


def adjust_rr_for_delta(
    entry: float,
    sl: float,
    tp2: float,
    maker_pct: float,
    taker_pct: float,
    gst_multiplier: float,
) -> float:
    """Recompute TP2 R:R using Delta fees plus GST.

    TP2 assumes maker fees on both entry and limit TP. Stop loss assumes
    maker entry plus taker stop-market execution.
    """
    em = maker_pct * gst_multiplier / 100.0
    et = taker_pct * gst_multiplier / 100.0
    tp_cost = 2.0 * em * entry
    sl_cost = (em + et) * entry
    risk = abs(entry - sl) + sl_cost
    reward = abs(tp2 - entry) - tp_cost
    if risk <= 0:
        raise ValueError("Delta R:R risk must be positive")
    return reward / risk


def to_delta_fields(signal: Any, spec: Any, *, fees: dict[str, float] | None = None) -> dict[str, Any]:
    """Return Delta-ready fields for a Signal and Delta product spec."""
    fees = fees or {"maker_pct": 0.020, "taker_pct": 0.050, "gst_multiplier": 1.18}
    entry_low = round_to_tick(signal.entry_low, spec.tick_size)
    entry_high = round_to_tick(signal.entry_high, spec.tick_size)
    stop_loss = round_to_tick(signal.stop_loss, spec.tick_size)
    tp1 = round_to_tick(signal.tp1, spec.tick_size)
    tp2 = round_to_tick(signal.tp2, spec.tick_size)
    tp3 = round_to_tick(signal.tp3, spec.tick_size)
    tp4 = round_to_tick(signal.tp4, spec.tick_size)
    entry = (entry_low + entry_high) / 2.0
    return {
        "delta_symbol": spec.symbol,
        "delta_contracts": binance_qty_to_delta_contracts(
            signal.size_units_advisory, spec.contract_value, spec.min_size
        ),
        "delta_entry_low": entry_low,
        "delta_entry_high": entry_high,
        "delta_stop_loss": stop_loss,
        "delta_tp1": tp1,
        "delta_tp2": tp2,
        "delta_tp3": tp3,
        "delta_tp4": tp4,
        "delta_rr_tp2": adjust_rr_for_delta(
            entry, stop_loss, tp2, fees["maker_pct"], fees["taker_pct"], fees["gst_multiplier"]
        ),
    }
