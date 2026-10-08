from __future__ import annotations

import httpx
import pytest

from app.exchanges.delta_products import DeltaProductsClient


_PAYLOAD = {
    "meta": {"after": None, "limit": 3000, "before": None, "total_count": 2},
    "success": True,
    "result": [
        {
            "symbol": "BTCUSD", "contract_value": "0.001", "tick_size": "0.50",
            "position_size_limit": 100000, "position_notional_limit": "100000",
            "trading_status": "operational", "maker_commission_rate": "0.0002",
            "taker_commission_rate": "0.0005",
            "underlying_asset": {"symbol": "BTC"}, "quoting_asset": {"symbol": "USD"},
        },
        {
            "symbol": "ETHUSD", "contract_value": "0.01", "tick_size": "0.05",
            "position_size_limit": 50000, "position_notional_limit": "100000",
            "trading_status": "operational", "maker_commission_rate": "0.0002",
            "taker_commission_rate": "0.0005",
            "underlying_asset": {"symbol": "ETH"}, "quoting_asset": {"symbol": "USD"},
        },
    ],
}


@pytest.mark.asyncio
async def test_fetch_products_parses_live_nested_result_schema():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_PAYLOAD)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://delta.test") as http:
        client = DeltaProductsClient("https://delta.test", 3600, client=http)
        products = await client.fetch_products()
    assert calls == 1
    assert products["BTCUSD"].underlying == "BTC"
    assert products["BTCUSD"].quoting == "USD"
    assert products["BTCUSD"].contract_value == 0.001
    assert products["BTCUSD"].position_size_limit == 100000
    assert products["BTCUSD"].maker_rate == 0.0002
    assert products["BTCUSD"].raw["symbol"] == "BTCUSD"


@pytest.mark.asyncio
async def test_fetch_products_uses_cache_until_ttl_expires(monkeypatch):
    calls = 0
    now = 100.0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_PAYLOAD)

    monkeypatch.setattr("app.exchanges.delta_products.time.monotonic", lambda: now)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://delta.test") as http:
        client = DeltaProductsClient("https://delta.test", 10, client=http)
        await client.fetch_products()
        await client.fetch_products()
        assert calls == 1
        now = 111.0
        await client.fetch_products()
    assert calls == 2


@pytest.mark.asyncio
async def test_fetch_failure_keeps_stale_cache_and_does_not_raise():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=_PAYLOAD)
        return httpx.Response(503, text="temporarily unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://delta.test") as http:
        client = DeltaProductsClient("https://delta.test", 1, client=http)
        first = await client.fetch_products()
        client._cached_at = 0.0
        second = await client.fetch_products()
    assert second == first
    assert "BTCUSD" in client.get_cached()
