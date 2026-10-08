"""Pure conversion of Binance-format advisory signals to Delta fields.

This module deliberately performs no I/O, authentication, or order activity.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any


_KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "USD")


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _as_float(value: Decimal) -> float:
    return float(value)


def binance_qty_to_delta_contracts(
    binance_qty: float,
    contract_value: float,
    max_contracts: int,
) -> float:
    """Convert base-asset quantity to whole Delta contracts and cap per order."""
    if binance_qty <= 0 or contract_value <= 0 or max_contracts <= 0:
        return 0.0
    calculated = (_decimal(binance_qty) / _decimal(contract_value)).to_integral_value(rounding=ROUND_DOWN)
    capped = min(calculated, _decimal(max_contracts))
    return _as_float(capped) if capped > 0 else 0.0


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
    maker_rate: float,
    taker_rate: float,
    gst_multiplier: float,
) -> float:
    """Recompute TP2 R:R using decimal API fee rates plus GST.

    ``maker_rate`` and ``taker_rate`` are decimal rates, e.g. 0.0002
    and 0.0005, as returned by Delta's products API.
    """
    em = maker_rate * gst_multiplier
    et = taker_rate * gst_multiplier
    tp_cost = 2.0 * em * entry
    sl_cost = (em + et) * entry
    risk = abs(entry - sl) + sl_cost
    reward = abs(tp2 - entry) - tp_cost
    if risk <= 0:
        raise ValueError("Delta R:R risk must be positive")
    return reward / risk


def resolve_delta_symbol(binance_symbol: str, products: dict[str, Any]) -> Any | None:
    """Resolve a Binance symbol such as BTCUSDT to an operational Delta spec."""
    symbol = str(binance_symbol).upper().strip()
    base = None
    for quote in _KNOWN_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[:-len(quote)]
            break
    if not base:
        return None
    exact = products.get(f"{base}USD")
    if (
        exact is not None
        and str(getattr(exact, "underlying", "")).upper() == base
        and str(getattr(exact, "quoting", "")).upper() in {"USD", "USDT"}
        and str(getattr(exact, "trading_status", "")).lower() == "operational"
    ):
        return exact
    for spec in products.values():
        if (
            str(getattr(spec, "underlying", "")).upper() == base
            and str(getattr(spec, "quoting", "")).upper() in {"USD", "USDT"}
            and str(getattr(spec, "trading_status", "")).lower() == "operational"
        ):
            return spec
    return None


def to_delta_fields(signal: Any, spec: Any, *, fees: dict[str, float] | None = None) -> dict[str, Any]:
    """Return Delta-ready fields for a Signal and a resolved Delta product spec."""
    fees = fees or {"maker_pct": 0.020, "taker_pct": 0.050, "gst_multiplier": 1.18}
    maker_rate = float(getattr(spec, "maker_rate", 0.0) or 0.0) or float(fees["maker_pct"]) / 100.0
    taker_rate = float(getattr(spec, "taker_rate", 0.0) or 0.0) or float(fees["taker_pct"]) / 100.0
    gst_multiplier = float(fees["gst_multiplier"])
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
            signal.size_units_advisory, spec.contract_value, spec.position_size_limit
        ),
        "delta_entry_low": entry_low,
        "delta_entry_high": entry_high,
        "delta_stop_loss": stop_loss,
        "delta_tp1": tp1,
        "delta_tp2": tp2,
        "delta_tp3": tp3,
        "delta_tp4": tp4,
        "delta_rr_tp2": adjust_rr_for_delta(
            entry, stop_loss, tp2, maker_rate, taker_rate, gst_multiplier
        ),
    }
