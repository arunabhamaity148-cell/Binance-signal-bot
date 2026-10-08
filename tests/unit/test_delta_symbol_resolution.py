from __future__ import annotations

from types import SimpleNamespace

from app.exchanges.delta_converter import resolve_delta_symbol


def _spec(symbol, underlying, quoting="USD", status="operational"):
    return SimpleNamespace(symbol=symbol, underlying=underlying, quoting=quoting, trading_status=status)


def test_btcusdt_resolves_to_btcusd():
    products = {"BTCUSD": _spec("BTCUSD", "BTC")}
    assert resolve_delta_symbol("BTCUSDT", products).symbol == "BTCUSD"


def test_btcusdt_prefers_exact_perpetual_symbol_over_btc_option():
    products = {
        "C-BTC-88000": _spec("C-BTC-88000", "BTC"),
        "BTCUSD": _spec("BTCUSD", "BTC"),
    }
    assert resolve_delta_symbol("BTCUSDT", products).symbol == "BTCUSD"


def test_galausdt_resolves_to_galausd():
    products = {"GALAUSD": _spec("GALAUSD", "GALA")}
    assert resolve_delta_symbol("GALAUSDT", products).symbol == "GALAUSD"


def test_unknown_or_nonoperational_symbol_returns_none():
    products = {
        "BTCUSD": _spec("BTCUSD", "BTC", status="suspended"),
        "ETHUSD": _spec("ETHUSD", "ETH"),
    }
    assert resolve_delta_symbol("BTCUSDT", products) is None
    assert resolve_delta_symbol("XRPUSDT", products) is None
