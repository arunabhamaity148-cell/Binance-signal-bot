from __future__ import annotations

import aiosqlite
import pytest

from app.database.migrations import _MIGRATIONS, apply_migrations, get_current_version
from app.database.repository import SignalRepository


@pytest.mark.asyncio
async def test_apply_migrations_on_fresh_db(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = await aiosqlite.connect(str(db_path))
    try:
        version = await apply_migrations(conn, now_ms=1000)
        assert version == 3

        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = {row[0] async for row in cursor}
        assert "signals" in tables
        assert "lifecycle_events" in tables
        assert "candidate_audit" in tables
        assert "outcomes" in tables
        assert "runtime_events" in tables
        assert "schema_migrations" in tables
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_apply_migrations_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    conn = await aiosqlite.connect(str(db_path))
    try:
        v1 = await apply_migrations(conn, now_ms=1000)
        v2 = await apply_migrations(conn, now_ms=2000)
        assert v1 == v2 == 3
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_current_version_zero_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = await aiosqlite.connect(str(db_path))
    try:
        version = await get_current_version(conn)
        assert version == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_existing_version_one_database_upgrades_to_three(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "upgrade.db"))
    try:
        await conn.executescript(_MIGRATIONS[0][1])
        await conn.execute("INSERT INTO schema_migrations(version, applied_ts_ms) VALUES (1, 1000)")
        await conn.commit()
        assert await get_current_version(conn) == 1
        assert await apply_migrations(conn, now_ms=2000) == 3
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='outcomes'") as cursor:
            assert await cursor.fetchone() == ("outcomes",)
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_events'") as cursor:
            assert await cursor.fetchone() == ("runtime_events",)
        assert await apply_migrations(conn, now_ms=3000) == 3
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_g16_veto_block_is_allowed_by_repository_and_sqlite_check(tmp_path):
    repo = SignalRepository(tmp_path / "g16.db")
    await repo.connect()
    try:
        await repo.record_veto_block(
            guard_name="G16",
            symbol="BTCUSDT",
            strategy_source="S1",
            event_ts_ms=1000,
            message="multi-timeframe confluence block",
        )
        async with repo._conn.execute(
            "SELECT guard_name FROM runtime_events WHERE event_type='VETO_BLOCK'"
        ) as cursor:
            assert await cursor.fetchone() == ("G16",)
        with pytest.raises(ValueError, match="invalid guard_name"):
            await repo.record_veto_block(
                guard_name="G17",
                symbol="BTCUSDT",
                strategy_source="S1",
                event_ts_ms=1001,
                message="invalid guard",
            )
    finally:
        await repo.close()
