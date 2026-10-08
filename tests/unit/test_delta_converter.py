from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exchanges.delta_converter import (
    adjust_rr_for_delta,
    binance_qty_to_delta_contracts,
    round_to_tick,
    to_delta_fields,
)


def _signal(**overrides):
    values = dict(
        symbol="BTCUSDT", size_units_advisory=0.05,
        entry_low=62150.25, entry_high=62200.24, stop_loss=61920.26,
        tp1=62400.24, tp2=62650.26, tp3=63000.24, tp4=63400.26,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_binance_qty_to_delta_contracts_rounds_down_and_rejects_below_minimum():
    assert binance_qty_to_delta_contracts(0.05, 0.001, 1) == 50.0
    assert binance_qty_to_delta_contracts(0.00199, 0.001, 1) == 1.0
    assert binance_qty_to_delta_contracts(0.00099, 0.001, 1) == 0.0


def test_round_to_tick_uses_round_half_up():
    assert round_to_tick(10.0, 0.5) == 10.0
    assert round_to_tick(10.25, 0.5) == 10.5
    assert round_to_tick(10.24, 0.5) == 10.0
    with pytest.raises(ValueError):
        round_to_tick(10.0, 0.0)


def test_adjust_rr_applies_gst_and_distinct_maker_taker_costs():
    result = adjust_rr_for_delta(100.0, 90.0, 120.0, 0.020, 0.050, 1.18)
    em = 0.020 * 1.18 / 100
    et = 0.050 * 1.18 / 100
    expected = (20.0 - 2 * em * 100.0) / (10.0 + (em + et) * 100.0)
    assert result == pytest.approx(expected)


def test_to_delta_fields_maps_contracts_and_tick_aligned_prices():
    spec = SimpleNamespace(symbol="BTCUSDT", contract_value=0.001, tick_size=0.50, min_size=1.0, max_size=1_000_000.0)
    fields = to_delta_fields(_signal(), spec)
    assert fields["delta_symbol"] == "BTCUSDT"
    assert fields["delta_contracts"] == 50.0
    assert fields["delta_entry_low"] == 62150.5
    assert fields["delta_entry_high"] == 62200.0
    assert fields["delta_stop_loss"] == 61920.5
    assert fields["delta_tp2"] == 62650.5
    assert fields["delta_rr_tp2"] > 0
