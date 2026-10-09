from pathlib import Path

import aiosqlite
import pytest

from app.database.migrations import apply_migrations, get_current_version
from app.database.repository import SignalRepository


@pytest.mark.asyncio
async def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "runtime.db"
    conn = await aiosqlite.connect(db)
    try:
        assert await get_current_version(conn) == 0
        assert await apply_migrations(conn, now_ms=1000) == 3
        assert await apply_migrations(conn, now_ms=2000) == 3
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_active_veto_block_is_allowed_and_removed_guard_is_rejected(tmp_path):
    repo = SignalRepository(tmp_path / "guards.db")
    await repo.connect()
    try:
        await repo.record_veto_block(
            guard_name="G9", symbol="BTCUSDT", strategy_source="S1",
            event_ts_ms=1000, message="BTC regime degrade",
        )
        with pytest.raises(ValueError, match="invalid guard_name"):
            await repo.record_veto_block(
                guard_name="G16", symbol="BTCUSDT", strategy_source="S1",
                event_ts_ms=1001, message="removed guard",
            )
    finally:
        await repo.close()
