"""Database migrations.

A minimal, linear migration system: each migration is a numbered SQL
script applied in order, tracked in a `schema_migrations` table. New
schema changes add numbered migrations rather than editing earlier
ones, so existing deployments can upgrade in place.
"""

from __future__ import annotations

import aiosqlite

from app.core.errors import MigrationError

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_ts_ms INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            created_ts_ms INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            grade TEXT NOT NULL,
            confidence REAL NOT NULL,
            strategy_source TEXT NOT NULL,
            entry_low REAL NOT NULL,
            entry_high REAL NOT NULL,
            stop_loss REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            tp4 REAL NOT NULL,
            rr_tp2 REAL NOT NULL,
            expiry_ts_ms INTEGER NOT NULL,
            why_lines_json TEXT NOT NULL,
            veto_state TEXT NOT NULL,
            veto_reason TEXT,
            advisory_warning TEXT NOT NULL,
            size_units_advisory REAL NOT NULL,
            notional_usd_advisory REAL NOT NULL,
            meta_json TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_created_ts ON signals(created_ts_ms);
        CREATE INDEX IF NOT EXISTS idx_signals_lifecycle_state ON signals(lifecycle_state);

        CREATE TABLE IF NOT EXISTS lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            reason TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
        );

        CREATE INDEX IF NOT EXISTS idx_lifecycle_signal_id ON lifecycle_events(signal_id);

        CREATE TABLE IF NOT EXISTS candidate_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_source TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            event_ts_ms INTEGER NOT NULL,
            snapshot_version TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            created_ts_ms INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_audit_symbol ON candidate_audit(symbol);
        CREATE INDEX IF NOT EXISTS idx_candidate_audit_strategy ON candidate_audit(strategy_source);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS outcomes (
            signal_id TEXT PRIMARY KEY NOT NULL,
            realized_r REAL NOT NULL,
            recorded_ts_ms INTEGER NOT NULL,
            provenance TEXT NOT NULL CHECK (provenance = 'manual'),
            note TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
        );
        CREATE INDEX IF NOT EXISTS idx_outcomes_recorded_ts ON outcomes(recorded_ts_ms);
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS runtime_events (
            event_id TEXT PRIMARY KEY NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('VETO_BLOCK', 'ERROR')),
            created_ts_ms INTEGER NOT NULL,
            severity TEXT,
            guard_name TEXT,
            symbol TEXT,
            strategy_source TEXT,
            source TEXT,
            exception_type TEXT,
            message TEXT NOT NULL,
            CHECK (
                (event_type = 'VETO_BLOCK' AND guard_name IN ('G1','G2','G4','G5','G6','G8','G9'))
                OR (event_type = 'ERROR' AND severity IN ('ERROR','CRITICAL'))
            )
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_events_type_created
            ON runtime_events(event_type, created_ts_ms);
        CREATE INDEX IF NOT EXISTS idx_runtime_events_guard_created
            ON runtime_events(guard_name, created_ts_ms);
        """,
    ),
]


async def get_current_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return 0
    async with conn.execute("SELECT MAX(version) FROM schema_migrations") as cursor:
        row = await cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


async def apply_migrations(conn: aiosqlite.Connection, *, now_ms: int) -> int:
    """Applies all pending migrations in order. Returns the final
    schema version. Raises MigrationError if any migration fails."""
    current = await get_current_version(conn)
    applied_count = 0
    for version, script in _MIGRATIONS:
        if version <= current:
            continue
        try:
            await conn.executescript(script)
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_ts_ms) VALUES (?, ?)",
                (version, now_ms),
            )
            await conn.commit()
            applied_count += 1
        except Exception as exc:  # noqa: BLE001 - any failure here is a hard migration failure
            await conn.rollback()
            raise MigrationError(f"migration {version} failed: {exc}") from exc
    final_version = await get_current_version(conn)
    return final_version
