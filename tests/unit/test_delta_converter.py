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


def _spec(**overrides):
    values = dict(
        symbol="BTCUSD", underlying="BTC", quoting="USD", contract_value=0.001,
        tick_size=0.50, position_size_limit=100_000, position_notional_limit=100_000.0,
        maker_rate=0.0002, taker_rate=0.0005, trading_status="operational",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_binance_qty_to_delta_contracts_rounds_down_and_caps_at_position_limit():
    assert binance_qty_to_delta_contracts(0.05, 0.001, 100) == 50.0
    assert binance_qty_to_delta_contracts(0.00199, 0.001, 100) == 1.0
    assert binance_qty_to_delta_contracts(200.0, 0.001, 100) == 100.0
    assert binance_qty_to_delta_contracts(0.00099, 0.001, 100) == 0.0


def test_round_to_tick_uses_round_half_up():
    assert round_to_tick(10.0, 0.5) == 10.0
    assert round_to_tick(10.25, 0.5) == 10.5
    assert round_to_tick(10.24, 0.5) == 10.0
    with pytest.raises(ValueError):
        round_to_tick(10.0, 0.0)


def test_adjust_rr_applies_decimal_api_rates_and_gst():
    result = adjust_rr_for_delta(100.0, 90.0, 120.0, 0.0002, 0.0005, 1.18)
    em = 0.0002 * 1.18
    et = 0.0005 * 1.18
    expected = (20.0 - 2 * em * 100.0) / (10.0 + (em + et) * 100.0)
    assert result == pytest.approx(expected)


def test_to_delta_fields_uses_api_rates_and_delta_symbol():
    fields = to_delta_fields(_signal(), _spec())
    assert fields["delta_symbol"] == "BTCUSD"
    assert fields["delta_contracts"] == 50.0
    assert fields["delta_entry_low"] == 62150.5
    assert fields["delta_entry_high"] == 62200.0
    assert fields["delta_stop_loss"] == 61920.5
    assert fields["delta_tp2"] == 62650.5
    assert fields["delta_rr_tp2"] > 0


def test_to_delta_fields_falls_back_to_config_fees_when_api_rates_missing():
    fields = to_delta_fields(_signal(), _spec(maker_rate=0.0, taker_rate=0.0),
                             fees={"maker_pct": 0.020, "taker_pct": 0.050, "gst_multiplier": 1.18})
    assert fields["delta_rr_tp2"] > 0
