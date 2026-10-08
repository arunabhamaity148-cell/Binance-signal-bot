from __future__ import annotations

import ast
import inspect
import json

import pytest

from app.core.errors import DataIntegrityError
from app.core.math import OHLC
from app.backtest.harness import load_bars_jsonl, write_bars_jsonl
import app.backtest.harness as harness_module


def _write_jsonl(path, rows):
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_load_bars_jsonl_basic(tmp_path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
        {"ts_ms": 2000, "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 12},
    ])
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert len(data.bars) == 2
    assert data.bars[0].close_time_ms == 1000
    assert data.bars[1].close_time_ms == 2000


def test_load_bars_jsonl_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_bars_jsonl(tmp_path / "does_not_exist.jsonl", symbol="BTCUSDT")


def test_load_bars_jsonl_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not valid json\n")
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_missing_required_field_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    _write_jsonl(p, [{"ts_ms": 1000, "open": 100, "high": 101, "low": 99}])  # missing close, volume
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_out_of_order_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 2000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
    ])
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_duplicate_ts_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
    ])
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_invalid_ohlc_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    _write_jsonl(p, [{"ts_ms": 1000, "open": 100, "high": 90, "low": 99, "close": 100.5, "volume": 10}])  # high < open
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_empty_file_raises(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_skips_blank_lines(tmp_path):
    p = tmp_path / "data.jsonl"
    with open(p, "w") as fh:
        fh.write(json.dumps({"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10}) + "\n")
        fh.write("\n")
        fh.write(json.dumps({"ts_ms": 2000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10}) + "\n")
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert len(data.bars) == 2


def test_load_bars_jsonl_with_orderbook_data(tmp_path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
         "orderbook": {"best_bid": 99.9, "best_ask": 100.1, "bid_depth_5lvl_usd": 500000, "ask_depth_5lvl_usd": 500000}},
    ])
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert 0 in data.orderbook_at_index
    assert data.orderbook_at_index[0].best_bid == 99.9


def test_load_bars_jsonl_orderbook_missing_field_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
         "orderbook": {"best_bid": 99.9}},
    ])
    with pytest.raises(DataIntegrityError):
        load_bars_jsonl(p, symbol="BTCUSDT")


def test_load_bars_jsonl_with_taker_flow_data(tmp_path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [
        {"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
         "taker_flow": {"taker_buy_base_last_bars": [1, 2, 3], "total_volume_last_bars": [2, 3, 4]}},
    ])
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert 0 in data.taker_flow_at_index
    assert data.taker_flow_at_index[0].taker_buy_base_last_bars == [1.0, 2.0, 3.0]


def test_load_bars_jsonl_without_auxiliary_data_leaves_maps_empty(tmp_path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"ts_ms": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10}])
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert data.orderbook_at_index == {}
    assert data.taker_flow_at_index == {}


def test_write_then_load_round_trip(tmp_path):
    bars = [
        OHLC(open=100, high=101, low=99, close=100.5, volume=10, close_time_ms=1000),
        OHLC(open=100.5, high=102, low=100, close=101, volume=12, close_time_ms=2000),
    ]
    p = tmp_path / "roundtrip.jsonl"
    write_bars_jsonl(p, bars)
    data = load_bars_jsonl(p, symbol="BTCUSDT")
    assert len(data.bars) == 2
    assert data.bars[0].close == 100.5
    assert data.bars[1].close == 101


def test_write_bars_jsonl_creates_parent_directories(tmp_path):
    p = tmp_path / "nested" / "dir" / "data.jsonl"
    bars = [OHLC(open=100, high=101, low=99, close=100.5, volume=10, close_time_ms=1000)]
    write_bars_jsonl(p, bars)
    assert p.exists()


# ---------------------------------------------------------------------------
# No live I/O: structural proof, not just absence of obvious calls
# ---------------------------------------------------------------------------


def test_harness_module_has_no_network_capable_imports():
    """Static AST check: the harness module must import nothing beyond
    json/dataclasses/pathlib and internal app.* modules -- no httpx,
    requests, websockets, socket, urllib, or any other network-capable
    library anywhere in its import list."""
    source = inspect.getsource(harness_module)
    tree = ast.parse(source)
    allowed_top_level = {"json", "dataclasses", "pathlib", "__future__"}
    forbidden_found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed_top_level and top != "app":
                    forbidden_found.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in allowed_top_level and top != "app":
                    forbidden_found.append(top)
    assert forbidden_found == [], f"harness.py imports network-capable or unexpected modules: {forbidden_found}"


def test_harness_module_defines_no_async_functions():
    """A stronger structural signal that this module cannot perform
    network I/O: it defines no async functions at all (every network
    client in this codebase, e.g. BinanceRestClient, NewsCollector, is
    async)."""
    source = inspect.getsource(harness_module)
    tree = ast.parse(source)
    async_funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert async_funcs == []
