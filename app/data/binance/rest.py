"""Binance USDⓈ-M public REST client.

Only the read-only endpoints enumerated in spec section 3 are
implemented. There is no method on this client that could place,
cancel, or modify an order, change leverage, or move funds — those
methods simply do not exist in this codebase (enforced statically by
scripts/scan_forbidden_calls.py).

No API key is ever sent. All requests are unauthenticated GETs against
public endpoints.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import RestClientError, RestRateLimitError, RestTimeoutError
from app.core.logging import get_logger
from app.data.binance.models import (
    RawExchangeInfoSymbol,
    RawFundingRate,
    RawKline,
    RawLongShortRatio,
    RawOpenInterest,
)

logger = get_logger(__name__)

TESTNET_REST_BASE_URL = "https://testnet.binancefuture.com"

_ALLOWED_PATHS = frozenset(
    {
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/klines",
        "/fapi/v1/depth",
        "/fapi/v1/trades",
        "/fapi/v1/aggTrades",
        "/fapi/v1/ticker/24hr",
        "/fapi/v1/ticker/bookTicker",
        "/fapi/v1/markPrice",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/fundingRate",
        "/fapi/v1/openInterest",
        "/futures/data/openInterestHist",
        "/futures/data/topLongShortAccountRatio",
        "/futures/data/topLongShortPositionRatio",
        "/futures/data/globalLongShortAccountRatio",
        "/futures/data/takerlongshortRatio",
    }
)


@dataclass(frozen=True)
class RestClientConfig:
    base_url: str
    timeout_s: float = 10.0
    max_retries: int = 4
    base_backoff_ms: int = 500
    jitter_ms: int = 250
    binance_env: str = "mainnet"

    def __post_init__(self) -> None:
        if self.binance_env not in {"mainnet", "testnet"}:
            raise ValueError("binance_env must be 'mainnet' or 'testnet'")
        if self.binance_env == "testnet":
            object.__setattr__(self, "base_url", TESTNET_REST_BASE_URL)


class BinanceRestClient:
    """Async, read-only Binance USDⓈ-M REST client.

    Every public method maps to exactly one allowed endpoint from
    spec section 3. `_get` enforces that only paths in `_ALLOWED_PATHS`
    can ever be requested, as a defense-in-depth measure on top of the
    fact that no other path is ever constructed anywhere in this
    class.
    """

    def __init__(self, config: RestClientConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_s
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path not in _ALLOWED_PATHS:
            raise RestClientError(f"path not in allowed read-only endpoint list: {path}")

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.get(path, params=params or {})
            except httpx.TimeoutException as exc:
                if attempt > self._config.max_retries:
                    raise RestTimeoutError(f"GET {path} timed out after {attempt} attempts") from exc
                await self._sleep_backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                if attempt > self._config.max_retries:
                    raise RestClientError(f"GET {path} failed: {exc}") from exc
                await self._sleep_backoff(attempt)
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                retry_after_s = float(retry_after) if retry_after else None
                if attempt > self._config.max_retries:
                    raise RestRateLimitError(f"GET {path} rate limited", retry_after_s=retry_after_s)
                await asyncio.sleep(retry_after_s if retry_after_s else self._backoff_delay_s(attempt))
                continue

            if 500 <= resp.status_code < 600:
                if attempt > self._config.max_retries:
                    raise RestClientError(f"GET {path} server error {resp.status_code}")
                await self._sleep_backoff(attempt)
                continue

            if resp.status_code != 200:
                raise RestClientError(f"GET {path} unexpected status {resp.status_code}: {resp.text}")

            try:
                return resp.json()
            except ValueError as exc:
                raise RestClientError(f"GET {path} returned non-JSON body: {exc}") from exc

    def _backoff_delay_s(self, attempt: int) -> float:
        base = self._config.base_backoff_ms * (2 ** (attempt - 1))
        jitter = random.uniform(0, self._config.jitter_ms)
        return (base + jitter) / 1000.0

    async def _sleep_backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff_delay_s(attempt))

    # -- Public read-only endpoint methods -----------------------------

    async def exchange_info(self) -> list[RawExchangeInfoSymbol]:
        data = await self._get("/fapi/v1/exchangeInfo")
        symbols = data.get("symbols", [])
        return [RawExchangeInfoSymbol.from_rest_payload(s) for s in symbols]

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[RawKline]:
        rows = await self._get(
            "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}
        )
        return [RawKline.from_rest_row(r) for r in rows]

    async def depth(self, symbol: str, limit: int = 20) -> dict:
        return await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    async def recent_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        return await self._get("/fapi/v1/trades", {"symbol": symbol, "limit": limit})

    async def agg_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        return await self._get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit})

    async def ticker_24hr(self, symbol: str) -> dict:
        return await self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})

    async def book_ticker(self, symbol: str) -> dict:
        return await self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})

    async def mark_price(self, symbol: str) -> dict:
        return await self._get("/fapi/v1/markPrice", {"symbol": symbol})

    async def premium_index(self, symbol: str) -> dict:
        return await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})

    @staticmethod
    def _history_params(
        symbol: str, limit: int, start_time_ms: int | None, end_time_ms: int | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        return params

    async def funding_rate(
        self, symbol: str, limit: int = 100, *,
        start_time_ms: int | None = None, end_time_ms: int | None = None,
    ) -> list[RawFundingRate]:
        rows = await self._get("/fapi/v1/fundingRate", self._history_params(symbol, limit, start_time_ms, end_time_ms))
        return [RawFundingRate.from_rest_row(r) for r in rows]

    async def open_interest(self, symbol: str) -> RawOpenInterest:
        row = await self._get("/fapi/v1/openInterest", {"symbol": symbol})
        return RawOpenInterest.from_rest_current(row)

    async def open_interest_hist(
        self, symbol: str, period: str, limit: int = 30, *,
        start_time_ms: int | None = None, end_time_ms: int | None = None,
    ) -> list[RawOpenInterest]:
        rows = await self._get(
            "/futures/data/openInterestHist",
            {**self._history_params(symbol, limit, start_time_ms, end_time_ms), "period": period},
        )
        return [RawOpenInterest.from_rest_row_hist(r) for r in rows]

    async def top_long_short_account_ratio(
        self, symbol: str, period: str, limit: int = 30
    ) -> list[RawLongShortRatio]:
        rows = await self._get(
            "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        return [RawLongShortRatio.from_rest_row(r) for r in rows]

    async def top_long_short_position_ratio(
        self, symbol: str, period: str, limit: int = 30
    ) -> list[RawLongShortRatio]:
        rows = await self._get(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        return [RawLongShortRatio.from_rest_row(r) for r in rows]

    async def global_long_short_account_ratio(
        self, symbol: str, period: str, limit: int = 30, *,
        start_time_ms: int | None = None, end_time_ms: int | None = None,
    ) -> list[RawLongShortRatio]:
        rows = await self._get(
            "/futures/data/globalLongShortAccountRatio",
            {**self._history_params(symbol, limit, start_time_ms, end_time_ms), "period": period},
        )
        return [RawLongShortRatio.from_rest_row(r) for r in rows]

    async def taker_long_short_ratio(
        self, symbol: str, period: str, limit: int = 30, *,
        start_time_ms: int | None = None, end_time_ms: int | None = None,
    ) -> list[RawLongShortRatio]:
        rows = await self._get(
            "/futures/data/takerlongshortRatio",
            {**self._history_params(symbol, limit, start_time_ms, end_time_ms), "period": period},
        )
        return [RawLongShortRatio.from_rest_row(r) for r in rows]
