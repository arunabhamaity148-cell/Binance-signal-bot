from __future__ import annotations

import pytest

from app.core.errors import DataIntegrityError
from app.data.binance.models import (
    RawAggTrade,
    RawBookTicker,
    RawDepthSnapshot,
    RawExchangeInfoSymbol,
    RawFundingRate,
    RawKline,
    RawLongShortRatio,
    RawOpenInterest,
)


def test_raw_kline_from_rest_row():
    row = [1000, "100.0", "101.0", "99.0", "100.5", "10.0", 1299, "5000.0", 42, "5.0", "500.0", "0"]
    k = RawKline.from_rest_row(row)
    assert k.open == 100.0
    assert k.high == 101.0
    assert k.low == 99.0
    assert k.close == 100.5
    assert k.volume == 10.0
    assert k.close_time_ms == 1299
    assert k.taker_buy_base_volume == 5.0
    assert k.is_closed is True


def test_raw_kline_from_rest_row_malformed_raises():
    with pytest.raises(DataIntegrityError):
        RawKline.from_rest_row([1000, "100.0"])


def test_raw_kline_from_ws_payload():
    payload = {
        "k": {
            "t": 1000, "T": 1299, "o": "100.0", "h": "101.0",
            "l": "99.0", "c": "100.5", "v": "10.0", "V": "5.0", "x": True,
        }
    }
    k = RawKline.from_ws_payload(payload)
    assert k.is_closed is True
    assert k.close == 100.5


def test_raw_kline_from_ws_payload_unclosed():
    payload = {
        "k": {
            "t": 1000, "T": 1299, "o": "100.0", "h": "101.0",
            "l": "99.0", "c": "100.5", "v": "10.0", "V": "5.0", "x": False,
        }
    }
    k = RawKline.from_ws_payload(payload)
    assert k.is_closed is False


def test_raw_kline_from_ws_payload_missing_k_raises():
    with pytest.raises(DataIntegrityError):
        RawKline.from_ws_payload({"not_k": {}})


def test_raw_kline_from_ws_payload_missing_field_raises():
    payload = {"k": {"t": 1000, "T": 1299}}
    with pytest.raises(DataIntegrityError):
        RawKline.from_ws_payload(payload)


def test_raw_agg_trade_from_ws_payload():
    payload = {"a": 12345, "p": "100.5", "q": "2.0", "m": False, "T": 1000}
    t = RawAggTrade.from_ws_payload(payload)
    assert t.agg_trade_id == 12345
    assert t.price == 100.5
    assert t.quantity == 2.0
    assert t.is_buyer_maker is False
    assert t.is_taker_buy is True  # buyer is not maker -> taker was buyer


def test_raw_agg_trade_is_taker_buy_false_when_buyer_is_maker():
    payload = {"a": 1, "p": "100.0", "q": "1.0", "m": True, "T": 1000}
    t = RawAggTrade.from_ws_payload(payload)
    assert t.is_taker_buy is False


def test_raw_agg_trade_missing_field_raises():
    with pytest.raises(DataIntegrityError):
        RawAggTrade.from_ws_payload({"a": 1, "p": "1.0"})


def test_raw_book_ticker_from_ws_payload():
    payload = {"s": "BTCUSDT", "b": "100.0", "B": "1.0", "a": "100.1", "A": "1.0", "E": 1000}
    bt = RawBookTicker.from_ws_payload(payload)
    assert bt.symbol == "BTCUSDT"
    assert bt.best_bid == 100.0
    assert bt.best_ask == 100.1


def test_raw_book_ticker_missing_field_raises():
    with pytest.raises(DataIntegrityError):
        RawBookTicker.from_ws_payload({"s": "BTCUSDT"})


def test_raw_depth_snapshot_from_ws_payload():
    payload = {
        "b": [["100.0", "1.0"], ["99.9", "2.0"]],
        "a": [["100.1", "1.5"], ["100.2", "2.5"]],
        "E": 1000,
        "u": 555,
    }
    depth = RawDepthSnapshot.from_ws_payload(payload, symbol="BTCUSDT")
    assert depth.symbol == "BTCUSDT"
    assert len(depth.bids) == 2
    assert depth.bids[0].price == 100.0
    assert depth.last_update_id == 555


def test_raw_depth_snapshot_missing_field_raises():
    with pytest.raises(DataIntegrityError):
        RawDepthSnapshot.from_ws_payload({"b": []}, symbol="BTCUSDT")


def test_raw_funding_rate_from_rest_row():
    row = {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1000}
    fr = RawFundingRate.from_rest_row(row)
    assert fr.funding_rate == 0.0001
    assert fr.funding_time_ms == 1000


def test_raw_funding_rate_missing_field_raises():
    with pytest.raises(DataIntegrityError):
        RawFundingRate.from_rest_row({"symbol": "BTCUSDT"})


def test_raw_open_interest_from_rest_row_hist():
    row = {"symbol": "BTCUSDT", "sumOpenInterest": "12345.6", "timestamp": 1000}
    oi = RawOpenInterest.from_rest_row_hist(row)
    assert oi.open_interest == 12345.6


def test_raw_open_interest_from_rest_current():
    row = {"symbol": "BTCUSDT", "openInterest": "999.0", "time": 2000}
    oi = RawOpenInterest.from_rest_current(row)
    assert oi.open_interest == 999.0
    assert oi.timestamp_ms == 2000


def test_raw_long_short_ratio_from_rest_row():
    row = {"symbol": "BTCUSDT", "longShortRatio": "1.5", "timestamp": 1000}
    r = RawLongShortRatio.from_rest_row(row)
    assert r.long_short_ratio == 1.5


def test_raw_exchange_info_symbol_from_rest_payload():
    entry = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        ],
    }
    sym = RawExchangeInfoSymbol.from_rest_payload(entry)
    assert sym.symbol == "BTCUSDT"
    assert sym.status == "TRADING"
    assert sym.price_tick == 0.10
    assert sym.qty_step == 0.001
    assert sym.min_qty == 0.001


def test_raw_exchange_info_symbol_missing_filters_raises():
    entry = {"symbol": "BTCUSDT", "status": "TRADING", "filters": []}
    with pytest.raises(DataIntegrityError):
        RawExchangeInfoSymbol.from_rest_payload(entry)
