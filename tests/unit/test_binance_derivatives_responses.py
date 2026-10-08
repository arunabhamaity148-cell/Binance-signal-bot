from __future__ import annotations

import httpx
import pytest

from app.data.binance.rest import BinanceRestClient, RestClientConfig


OPEN_INTEREST_HIST_JSON = '[{"symbol":"BTCUSDT","sumOpenInterest":"12345.6","sumOpenInterestValue":"987654321.0","CMCCirculatingSupply":"123456.0","timestamp":1728394800000}]'
TOP_ACCOUNT_RATIO_JSON = '[{"symbol":"BTCUSDT","longShortRatio":"1.2345","longAccount":"0.55","shortAccount":"0.45","timestamp":1728394800000}]'
TOP_POSITION_RATIO_JSON = '[{"symbol":"BTCUSDT","longShortRatio":"1.2345","longAccount":"0.60","shortAccount":"0.40","timestamp":1728394800000}]'
GLOBAL_ACCOUNT_RATIO_JSON = '[{"symbol":"BTCUSDT","longShortRatio":"1.2345","longAccount":"0.52","shortAccount":"0.48","timestamp":1728394800000}]'
TAKER_LONG_SHORT_RATIO_JSON = '[{"buySellRatio":"1.2345","buyVol":"12345.6","sellVol":"9876.5","timestamp":1728394800000}]'


def _client_for_response(body: str):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    http = httpx.AsyncClient(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(respond),
    )
    return BinanceRestClient(RestClientConfig("https://fapi.binance.com"), client=http), requests


@pytest.mark.asyncio
async def test_open_interest_hist_binance_response_shape():
    client, requests = _client_for_response(OPEN_INTEREST_HIST_JSON)
    try:
        rows = await client.open_interest_hist("BTCUSDT", "5m", limit=1)
    finally:
        await client.close()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].open_interest == 12345.6
    assert rows[0].timestamp_ms == 1728394800000
    assert requests[0].url.path == "/futures/data/openInterestHist"


@pytest.mark.asyncio
async def test_top_long_short_account_ratio_binance_response_shape():
    client, requests = _client_for_response(TOP_ACCOUNT_RATIO_JSON)
    try:
        rows = await client.top_long_short_account_ratio("BTCUSDT", "5m", limit=1)
    finally:
        await client.close()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].long_short_ratio == 1.2345
    assert rows[0].timestamp_ms == 1728394800000
    assert requests[0].url.path == "/futures/data/topLongShortAccountRatio"


@pytest.mark.asyncio
async def test_top_long_short_position_ratio_binance_response_shape():
    client, requests = _client_for_response(TOP_POSITION_RATIO_JSON)
    try:
        rows = await client.top_long_short_position_ratio("BTCUSDT", "5m", limit=1)
    finally:
        await client.close()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].long_short_ratio == 1.2345
    assert rows[0].timestamp_ms == 1728394800000
    assert requests[0].url.path == "/futures/data/topLongShortPositionRatio"


@pytest.mark.asyncio
async def test_global_long_short_account_ratio_binance_response_shape():
    client, requests = _client_for_response(GLOBAL_ACCOUNT_RATIO_JSON)
    try:
        rows = await client.global_long_short_account_ratio("BTCUSDT", "5m", limit=1)
    finally:
        await client.close()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].long_short_ratio == 1.2345
    assert rows[0].timestamp_ms == 1728394800000
    assert requests[0].url.path == "/futures/data/globalLongShortAccountRatio"


@pytest.mark.asyncio
async def test_taker_long_short_ratio_binance_response_shape_has_no_body_symbol():
    client, requests = _client_for_response(TAKER_LONG_SHORT_RATIO_JSON)
    try:
        rows = await client.taker_long_short_ratio("BTCUSDT", "5m", limit=1)
    finally:
        await client.close()

    row = rows[0]
    assert row.symbol == "BTCUSDT"  # supplied from the request, not the response body
    assert row.taker_buy_sell_ratio == 1.2345
    assert row.taker_buy_vol == 12345.6
    assert row.taker_sell_vol == 9876.5
    assert row.ts_ms == 1728394800000
    assert requests[0].url.path == "/futures/data/takerlongshortRatio"
    assert requests[0].url.params["symbol"] == "BTCUSDT"
