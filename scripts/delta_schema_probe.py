#!/usr/bin/env python3
"""Probe Delta's public products schema without credentials or trading calls."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from app.exchanges.delta_products import DeltaProductsClient

BASE_URL = "https://api.india.delta.exchange"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0, verify=True) as http:
        client = DeltaProductsClient(BASE_URL, 3600, client=http)
        try:
            response = await http.get("/v2/products")
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("result", []) if isinstance(payload, dict) else []
            sys.stdout.write(f"top-level keys: {sorted(payload.keys()) if isinstance(payload, dict) else []}\n")
            sys.stdout.write("first 3 raw rows:\n")
            for row in rows[:3]:
                sys.stdout.write(json.dumps(row, sort_keys=True) + "\n")
            parsed = client._parse_products(rows, payload)
            sys.stdout.write(f"parsed count: {len(parsed)}\n")
            sys.stdout.write(f"BTCUSD found: {'yes' if 'BTCUSD' in parsed else 'no'}\n")
            return 0
        except Exception as exc:
            sys.stderr.write(f"Delta schema probe failed: {type(exc).__name__}: {exc}\n")
            return 1
        finally:
            await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
