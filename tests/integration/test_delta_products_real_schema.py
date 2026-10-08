from __future__ import annotations

import httpx
import pytest

from app.exchanges.delta_products import DeltaProductsClient


REAL_SCHEMA_FIXTURE = {
    "meta": {"after": None, "limit": 3000, "before": None, "total_count": 1274},
    "success": True,
    "result": [
        {
            "symbol": "GALAUSD",
            "contract_value": "100",
            "tick_size": "0.000001",
            "position_size_limit": 415000,
            "position_notional_limit": "100000",
            "state": "live",
            "trading_status": "operational",
            "default_leverage": 20,
            "maker_commission_rate": "0.0002",
            "taker_commission_rate": "0.0005",
            "underlying_asset": {"symbol": "GALA", "name": "Gala"},
            "quoting_asset": {"symbol": "USD", "name": "US Dollar"},
        }
    ],
}


@pytest.mark.asyncio
async def test_real_schema_fixture_parses_without_using_legacy_min_max_fields():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=REAL_SCHEMA_FIXTURE)),
        base_url="https://delta.test",
    ) as http:
        client = DeltaProductsClient("https://delta.test", 3600, client=http)
        products = await client.fetch_products()
    spec = products["GALAUSD"]
    assert spec.underlying == "GALA"
    assert spec.quoting == "USD"
    assert spec.contract_value == 100.0
    assert spec.tick_size == 0.000001
    assert spec.position_size_limit == 415000
    assert spec.position_notional_limit == 100000.0
    assert spec.maker_rate == 0.0002
    assert spec.taker_rate == 0.0005
    assert spec.trading_status == "operational"
