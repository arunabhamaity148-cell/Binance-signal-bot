from __future__ import annotations

import asyncio

import pytest

from app.bot import LiveSnapshotCache
from app.config import load_all


@pytest.mark.asyncio
async def test_resync_backfill_preserves_existing_cache_row_while_refresh_is_pending(monkeypatch):
    cache = LiveSnapshotCache(load_all(), rest=object())
    previous_row = {"existing_snapshot_marker": "keep"}
    cache.data["BTCUSDT"] = previous_row
    refresh_started = asyncio.Event()
    finish_refresh = asyncio.Event()

    async def blocked_refresh(symbol: str) -> None:
        assert symbol == "BTCUSDT"
        refresh_started.set()
        await finish_refresh.wait()

    monkeypatch.setattr(cache, "_backfill_one", blocked_refresh)
    task = asyncio.create_task(cache.backfill(["BTCUSDT"]))
    try:
        await refresh_started.wait()
        assert cache.data["BTCUSDT"] is previous_row
        assert cache.data["BTCUSDT"]["existing_snapshot_marker"] == "keep"
    finally:
        finish_refresh.set()
        await task
