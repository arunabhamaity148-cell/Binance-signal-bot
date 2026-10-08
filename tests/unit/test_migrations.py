from __future__ import annotations

import aiosqlite
import pytest

from app.database.migrations import apply_migrations, get_current_version


@pytest.mark.asyncio
async def test_apply_migrations_on_fresh_db(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = await aiosqlite.connect(str(db_path))
    try:
        version = await apply_migrations(conn, now_ms=1000)
        assert version == 1

        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = {row[0] async for row in cursor}
        assert "signals" in tables
        assert "lifecycle_events" in tables
        assert "candidate_audit" in tables
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
        assert v1 == v2 == 1
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
