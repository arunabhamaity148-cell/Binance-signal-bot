"""Read-only Delta Exchange products metadata client."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeltaProductSpec:
    symbol: str
    contract_value: float
    tick_size: float
    min_size: float
    max_size: float
    raw: dict[str, Any]


class DeltaProductsSchemaError(ValueError):
    """Raised when Delta's public products response is not the documented schema."""

    def __init__(self, message: str, raw_response: Any):
        super().__init__(message)
        self.raw_response = raw_response


class DeltaProductsClient:
    """Public Delta products client; it has no private API or trading methods."""

    def __init__(self, base_url: str, cache_ttl_seconds: int, *, products_path: str = "/v2/products",
                 client: httpx.AsyncClient | None = None):
        if not base_url.startswith("https://"):
            raise ValueError("Delta base_url must use HTTPS")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.products_path = products_path
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=10.0, verify=True)
        self._owns_client = client is None
        self._cached: dict[str, DeltaProductSpec] = {}
        self._cached_at = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def get_cached(self) -> dict[str, DeltaProductSpec]:
        return dict(self._cached)

    def _cache_fresh(self) -> bool:
        return bool(self._cached) and time.monotonic() - self._cached_at < self.cache_ttl_seconds

    async def fetch_products(self) -> dict[str, DeltaProductSpec]:
        if self._cache_fresh():
            return self.get_cached()
        try:
            payload = await self._fetch_with_retries()
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, list):
                raise DeltaProductsSchemaError(
                    "Delta products response must contain a result array", payload
                )
            parsed = self._parse_products(result, payload)
            self._cached = parsed
            self._cached_at = time.monotonic()
            return self.get_cached()
        except DeltaProductsSchemaError:
            raise
        except Exception as exc:  # fail closed while retaining stale metadata
            logger.error("Delta products fetch failed", extra={"context": {"error": f"{type(exc).__name__}: {exc}", "using_stale_cache": bool(self._cached)}})
            return self.get_cached()

    async def _fetch_with_retries(self) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = await self._client.get(self.products_path)
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                last_error = exc
                if attempt == 3:
                    raise
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.5 * (2 ** (attempt - 1))
                if attempt == 3:
                    raise RuntimeError("Delta products rate limit exhausted")
                await asyncio.sleep(delay)
                continue
            if 500 <= response.status_code < 600 and attempt < 3:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            if response.status_code != 200:
                raise RuntimeError(f"Delta products HTTP {response.status_code}: {response.text}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(f"Delta products returned non-JSON data: {exc}") from exc
            if not isinstance(payload, dict):
                raise DeltaProductsSchemaError("Delta products response must be an object", payload)
            return payload
        raise last_error or RuntimeError("Delta products fetch failed")

    @staticmethod
    def _parse_products(rows: list[Any], payload: Any) -> dict[str, DeltaProductSpec]:
        parsed: dict[str, DeltaProductSpec] = {}
        required = ("symbol", "contract_value", "tick_size", "min_size", "max_size")
        for row in rows:
            if not isinstance(row, dict) or any(key not in row for key in required):
                raise DeltaProductsSchemaError("Delta product row schema mismatch", payload)
            try:
                symbol = str(row["symbol"])
                values = {key: float(row[key]) for key in required[1:]}
            except (TypeError, ValueError) as exc:
                raise DeltaProductsSchemaError("Delta product numeric field is invalid", payload) from exc
            if not symbol or any(value <= 0 for value in values.values()):
                raise DeltaProductsSchemaError("Delta product contains non-positive fields", payload)
            parsed[symbol] = DeltaProductSpec(symbol=symbol, raw=dict(row), **values)
        return parsed
