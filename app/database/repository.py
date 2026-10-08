"""Database repository.

The only module in the codebase that issues raw SQL. Every other
module interacts with persistence through this repository's typed
methods, so a schema change is a one-file change, and so business
logic never has to know SQLite is the storage engine.

Every write is wrapped so that a failure raises DatabaseWriteError
rather than propagating a raw sqlite3/aiosqlite exception — callers
(main.py) can then handle persistence failures (spec section 24: SQLite
write failure, disk full) as a distinct, expected failure mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from app.core.errors import DatabaseWriteError, MigrationError
from app.core.time_utils import now_ms
from app.database.migrations import apply_migrations
from app.database.models import CandidateAuditRow, LifecycleEventRow, SignalRow
from app.signals.lifecycle import SignalLifecycleState
from app.signals.models import Signal


class SignalRepository:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        try:
            await apply_migrations(self._conn, now_ms=now_ms())
        except MigrationError:
            await self._conn.close()
            self._conn = None
            raise

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise DatabaseWriteError("repository is not connected; call connect() first")
        return self._conn

    async def insert_signal(self, signal: Signal) -> None:
        conn = self._require_conn()
        row = SignalRow(
            signal_id=signal.signal_id,
            created_ts_ms=signal.created_ts_ms,
            symbol=signal.symbol,
            direction=signal.direction,
            grade=signal.grade,
            confidence=signal.confidence,
            strategy_source=signal.strategy_source,
            entry_low=signal.entry_low,
            entry_high=signal.entry_high,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            tp4=signal.tp4,
            rr_tp2=signal.rr_tp2,
            expiry_ts_ms=signal.expiry_ts_ms,
            why_lines_json=json.dumps(signal.why_lines),
            veto_state=signal.veto_state,
            veto_reason=signal.veto_reason,
            advisory_warning=signal.advisory_warning,
            size_units_advisory=signal.size_units_advisory,
            notional_usd_advisory=signal.notional_usd_advisory,
            meta_json=json.dumps(signal.meta, default=str),
            lifecycle_state=SignalLifecycleState.PENDING.value,
        )
        try:
            await conn.execute(
                """
                INSERT INTO signals (
                    signal_id, created_ts_ms, symbol, direction, grade, confidence,
                    strategy_source, entry_low, entry_high, stop_loss, tp1, tp2, tp3, tp4,
                    rr_tp2, expiry_ts_ms, why_lines_json, veto_state, veto_reason,
                    advisory_warning, size_units_advisory, notional_usd_advisory,
                    meta_json, lifecycle_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.signal_id, row.created_ts_ms, row.symbol, row.direction, row.grade,
                    row.confidence, row.strategy_source, row.entry_low, row.entry_high,
                    row.stop_loss, row.tp1, row.tp2, row.tp3, row.tp4, row.rr_tp2,
                    row.expiry_ts_ms, row.why_lines_json, row.veto_state, row.veto_reason,
                    row.advisory_warning, row.size_units_advisory, row.notional_usd_advisory,
                    row.meta_json, row.lifecycle_state,
                ),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.rollback()
            raise DatabaseWriteError(f"failed to insert signal {signal.signal_id}: {exc}") from exc

    async def update_lifecycle_state(
        self, signal_id: str, from_state: str, to_state: str, ts_ms: int, reason: str | None = None
    ) -> None:
        conn = self._require_conn()
        try:
            await conn.execute(
                "UPDATE signals SET lifecycle_state = ? WHERE signal_id = ?",
                (to_state, signal_id),
            )
            await conn.execute(
                "INSERT INTO lifecycle_events (signal_id, from_state, to_state, ts_ms, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (signal_id, from_state, to_state, ts_ms, reason),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.rollback()
            raise DatabaseWriteError(f"failed to update lifecycle state for {signal_id}: {exc}") from exc

    async def insert_candidate_audit(
        self,
        *,
        symbol: str,
        strategy_source: str,
        direction: str,
        confidence: float,
        event_ts_ms: int,
        snapshot_version: str,
        meta: dict,
    ) -> None:
        conn = self._require_conn()
        try:
            await conn.execute(
                "INSERT INTO candidate_audit "
                "(symbol, strategy_source, direction, confidence, event_ts_ms, "
                "snapshot_version, meta_json, created_ts_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol, strategy_source, direction, confidence, event_ts_ms,
                    snapshot_version, json.dumps(meta, default=str), now_ms(),
                ),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.rollback()
            raise DatabaseWriteError(f"failed to insert candidate audit row: {exc}") from exc

    async def get_open_signals(self) -> list[SignalRow]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT signal_id, created_ts_ms, symbol, direction, grade, confidence, "
            "strategy_source, entry_low, entry_high, stop_loss, tp1, tp2, tp3, tp4, "
            "rr_tp2, expiry_ts_ms, why_lines_json, veto_state, veto_reason, "
            "advisory_warning, size_units_advisory, notional_usd_advisory, meta_json, "
            "lifecycle_state FROM signals WHERE lifecycle_state = ?",
            (SignalLifecycleState.PUBLISHED.value,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_signal_row(r) for r in rows]

    async def get_signal_by_id(self, signal_id: str) -> SignalRow | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT signal_id, created_ts_ms, symbol, direction, grade, confidence, "
            "strategy_source, entry_low, entry_high, stop_loss, tp1, tp2, tp3, tp4, "
            "rr_tp2, expiry_ts_ms, why_lines_json, veto_state, veto_reason, "
            "advisory_warning, size_units_advisory, notional_usd_advisory, meta_json, "
            "lifecycle_state FROM signals WHERE signal_id = ?",
            (signal_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_signal_row(row) if row else None

    async def count_signals_on_date(self, date_prefix_ts_start_ms: int, date_prefix_ts_end_ms: int) -> int:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ?",
            (date_prefix_ts_start_ms, date_prefix_ts_end_ms),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _row_to_signal_row(row) -> SignalRow:
        return SignalRow(
            signal_id=row[0], created_ts_ms=row[1], symbol=row[2], direction=row[3],
            grade=row[4], confidence=row[5], strategy_source=row[6], entry_low=row[7],
            entry_high=row[8], stop_loss=row[9], tp1=row[10], tp2=row[11], tp3=row[12],
            tp4=row[13], rr_tp2=row[14], expiry_ts_ms=row[15], why_lines_json=row[16],
            veto_state=row[17], veto_reason=row[18], advisory_warning=row[19],
            size_units_advisory=row[20], notional_usd_advisory=row[21], meta_json=row[22],
            lifecycle_state=row[23],
        )
