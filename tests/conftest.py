"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from app.core.math import OHLC


@pytest.fixture
def make_ohlc():
    """Factory fixture for building valid OHLC bars quickly in tests."""

    def _make(
        *,
        open: float = 100.0,
        high: float = 101.0,
        low: float = 99.0,
        close: float = 100.5,
        volume: float = 10.0,
        close_time_ms: int = 0,
    ) -> OHLC:
        return OHLC(
            open=open, high=high, low=low, close=close,
            volume=volume, close_time_ms=close_time_ms,
        )

    return _make


@pytest.fixture
def linear_bars(make_ohlc):
    """A simple, deterministic uptrending series of closed bars, useful
    as a baseline fixture across many unit tests."""

    def _make(n: int, start_price: float = 100.0, step: float = 1.0, start_ts_ms: int = 0, interval_ms: int = 300_000):
        bars = []
        price = start_price
        for i in range(n):
            o = price
            c = price + step
            h = max(o, c) + 0.1
            l = min(o, c) - 0.1
            bars.append(
                make_ohlc(
                    open=o, high=h, low=l, close=c, volume=100.0,
                    close_time_ms=start_ts_ms + i * interval_ms,
                )
            )
            price = c
        return bars

    return _make
