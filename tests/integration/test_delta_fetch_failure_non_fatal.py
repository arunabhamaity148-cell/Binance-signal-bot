from __future__ import annotations

import asyncio

import httpx
import pytest

from app.bot import SignalBot
from app.config import load_all
from app.exchanges.delta_products import DeltaProductsSchemaError
from app.signals.models import Signal


class _FakeDeltaClient:
    def __init__(self, failure):
        self.failure = failure

    async def fetch_products(self):
        raise self.failure

    def get_cached(self):
        return {}

    async def close(self):
        pass


def _signal():
    return Signal(
        signal_id="CSB-20261009-NODELTA", created_ts_ms=0, symbol="BTCUSDT", direction="LONG",
        grade="B", confidence=0.7, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["fixture"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=100.0, meta={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        DeltaProductsSchemaError("schema mismatch", {"result": [{"unexpected": True}]}),
        RuntimeError("HTTP 500"),
        asyncio.TimeoutError(),
    ],
    ids=["schema-mismatch", "http-500", "timeout"],
)
async def test_delta_fetch_failure_is_non_fatal_and_signal_stays_binance_only(monkeypatch, failure):
    cfg = load_all()
    monkeypatch.setattr("app.bot.DeltaProductsClient", lambda *args, **kwargs: _FakeDeltaClient(failure))
    bot = SignalBot(cfg, 2400.0, rest_client=object(), repository=object())
    await bot.initialize_delta_products()
    assert bot.delta_products == {}
    signal = bot._populate_delta_fields(_signal())
    assert signal.delta_available is False
    assert signal.delta_symbol is None
