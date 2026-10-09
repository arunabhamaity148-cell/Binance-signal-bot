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
import math
import uuid
from datetime import datetime, time as datetime_time, timedelta, timezone
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
        if self._conn is not None:
            return
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
    def _utc_day_bounds(ts_ms: int | None = None) -> tuple[int, int]:
        now = datetime.fromtimestamp((ts_ms if ts_ms is not None else now_ms()) / 1000, timezone.utc)
        start = datetime.combine(now.date(), datetime_time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    async def count_active_signals(self, *, now_ts_ms: int | None = None) -> int:
        """Count unexpired PUBLISHED signals (the schema's PAPER_ACTIVE state)."""
        now = now_ms() if now_ts_ms is None else int(now_ts_ms)
        conn = self._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE lifecycle_state = ? AND expiry_ts_ms > ?",
            (SignalLifecycleState.PUBLISHED.value, now),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0] if row else 0)

    async def count_signals_today(self, *, now_ts_ms: int | None = None) -> int:
        """Count all emitted signals today by created_ts_ms, including PENDING records."""
        start, end = self._utc_day_bounds(now_ts_ms)
        conn = self._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ?",
            (start, end),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0] if row else 0)

    async def count_active_by_cluster(
        self, cluster_symbols: list[str], *, now_ts_ms: int | None = None
    ) -> int:
        """Count unexpired PUBLISHED signals for symbols in a configured cluster."""
        if not cluster_symbols:
            return 0
        now = now_ms() if now_ts_ms is None else int(now_ts_ms)
        placeholders = ",".join("?" for _ in cluster_symbols)
        conn = self._require_conn()
        async with conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE lifecycle_state = ? AND expiry_ts_ms > ? AND symbol IN ({placeholders})",
            (SignalLifecycleState.PUBLISHED.value, now, *cluster_symbols),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0] if row else 0)

    async def seconds_since_last_signal(self, symbol: str, *, now_ts_ms: int | None = None) -> float | None:
        now = now_ms() if now_ts_ms is None else int(now_ts_ms)
        conn = self._require_conn()
        async with conn.execute(
            "SELECT MAX(created_ts_ms) FROM signals WHERE symbol = ?", (symbol,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] is None:
            return None
        return max(0.0, (now - int(row[0])) / 1000.0)

    async def last_signal_direction(self, symbol: str) -> str | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT direction FROM signals WHERE symbol = ? ORDER BY created_ts_ms DESC, rowid DESC LIMIT 1",
            (symbol,),
        ) as cursor:
            row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def daily_realized_loss_r(self, *, now_ts_ms: int | None = None) -> float:
        # This value is derived from operator-recorded outcomes. The system cannot
        # observe exchange fills or P&L. Returning 0.0 when no outcomes exist is an
        # explicit assumption: the operator has not recorded any losses yet. This is
        # consistent with the signal-only architecture — the operator is the source
        # of truth for realized P&L.
        start, end = self._utc_day_bounds(now_ts_ms)
        conn = self._require_conn()
        async with conn.execute(
            "SELECT COALESCE(SUM(realized_r), 0.0) FROM outcomes WHERE recorded_ts_ms >= ? AND recorded_ts_ms < ?",
            (start, end),
        ) as cursor:
            row = await cursor.fetchone()
        return float(row[0] if row else 0.0)

    async def record_outcome(
        self, signal_id: str, realized_r: float, note: str = "", *,
        provenance: str = "manual", recorded_ts_ms: int | None = None,
    ) -> None:
        """Record the one operator-supplied outcome for a signal; production provenance is manual-only."""
        if provenance != "manual":
            raise ValueError("outcome provenance must be 'manual'")
        if not math.isfinite(float(realized_r)):
            raise ValueError("realized_r must be finite")
        conn = self._require_conn()
        try:
            await conn.execute(
                "INSERT INTO outcomes (signal_id, realized_r, recorded_ts_ms, provenance, note) VALUES (?, ?, ?, ?, ?)",
                (signal_id, float(realized_r), int(recorded_ts_ms if recorded_ts_ms is not None else now_ms()), provenance, str(note)),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.rollback()
            raise DatabaseWriteError(f"failed to record manual outcome for {signal_id}: {exc}") from exc

    async def record_veto_block(
        self, *, guard_name: str, symbol: str, strategy_source: str,
        event_ts_ms: int, message: str,
    ) -> None:
        if guard_name not in {f"G{i}" for i in range(1, 17)}:
            raise ValueError(f"invalid guard_name: {guard_name!r}")
        await self._insert_runtime_event(
            event_type="VETO_BLOCK", created_ts_ms=event_ts_ms,
            guard_name=guard_name, symbol=symbol, strategy_source=strategy_source,
            message=message,
        )

    async def record_error_event(
        self, *, severity: str, source: str, exception_type: str,
        message: str, event_ts_ms: int | None = None,
    ) -> None:
        if severity not in {"ERROR", "CRITICAL"}:
            raise ValueError("severity must be ERROR or CRITICAL")
        await self._insert_runtime_event(
            event_type="ERROR", created_ts_ms=now_ms() if event_ts_ms is None else int(event_ts_ms),
            severity=severity, source=source, exception_type=exception_type, message=message,
        )

    async def _insert_runtime_event(self, **event) -> None:
        conn = self._require_conn()
        try:
            await conn.execute(
                "INSERT INTO runtime_events (event_id,event_type,created_ts_ms,severity,guard_name,"
                "symbol,strategy_source,source,exception_type,message) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, event["event_type"], event["created_ts_ms"],
                 event.get("severity"), event.get("guard_name"), event.get("symbol"),
                 event.get("strategy_source"), event.get("source"), event.get("exception_type"),
                 str(event["message"])[:4000]),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.rollback()
            raise DatabaseWriteError(f"failed to record runtime event: {exc}") from exc

    async def get_daily_report_data(self, *, start_ts_ms: int, end_ts_ms: int) -> dict:
        """Aggregate persisted signals, guard blocks, logged errors, and manual outcomes."""
        conn = self._require_conn()
        async def grouped(query: str, column: str) -> dict[str, int]:
            async with conn.execute(query, (start_ts_ms, end_ts_ms)) as cursor:
                return {str(row[0]): int(row[1]) for row in await cursor.fetchall()}

        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ?",
            (start_ts_ms, end_ts_ms),
        ) as cursor:
            row = await cursor.fetchone()
        signals_total = int(row[0] or 0)
        signals_by_grade = await grouped(
            "SELECT grade,COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ? GROUP BY grade", "grade")
        signals_by_strategy = await grouped(
            "SELECT strategy_source,COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ? GROUP BY strategy_source", "strategy")
        signals_by_symbol = await grouped(
            "SELECT symbol,COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ? GROUP BY symbol", "symbol")
        vetoes_by_guard = await grouped(
            "SELECT guard_name,COUNT(*) FROM runtime_events WHERE event_type='VETO_BLOCK' AND created_ts_ms >= ? AND created_ts_ms < ? GROUP BY guard_name", "guard")
        errors_by_severity = await grouped(
            "SELECT severity,COUNT(*) FROM runtime_events WHERE event_type='ERROR' AND created_ts_ms >= ? AND created_ts_ms < ? GROUP BY severity", "severity")

        async def outcome_aggregate(where: str = "", params: tuple = ()) -> dict:
            async with conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(realized_r),0),"
                "SUM(CASE WHEN realized_r>0 THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN realized_r<0 THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN realized_r=0 THEN 1 ELSE 0 END),"
                "COALESCE(SUM(CASE WHEN realized_r>0 THEN realized_r ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN realized_r<0 THEN realized_r ELSE 0 END),0) "
                f"FROM outcomes {where}", params,
            ) as cursor:
                values = await cursor.fetchone()
            return {"count": int(values[0] or 0), "realized_r": float(values[1] or 0.0),
                    "wins": int(values[2] or 0), "losses": int(values[3] or 0),
                    "flats": int(values[4] or 0), "gross_wins_r": float(values[5] or 0.0),
                    "gross_losses_r": float(values[6] or 0.0)}

        today = await outcome_aggregate(
            "WHERE recorded_ts_ms >= ? AND recorded_ts_ms < ?", (start_ts_ms, end_ts_ms))
        cumulative = await outcome_aggregate()
        return {"signals_total": signals_total, "signals_by_grade": signals_by_grade,
                "signals_by_strategy": signals_by_strategy, "signals_by_symbol": signals_by_symbol,
                "vetoes_by_guard": vetoes_by_guard, "errors_by_severity": errors_by_severity,
                "outcomes_today": today, "cumulative_outcomes": cumulative}

    async def get_hourly_summary_data(self, *, start_ts_ms: int, end_ts_ms: int,
                                      now_ts_ms: int | None = None) -> dict:
        """Read-only aggregates for the hourly operator summary."""
        conn = self._require_conn()
        async def grouped(query: str) -> dict[str, int]:
            async with conn.execute(query, (start_ts_ms, end_ts_ms)) as cursor:
                return {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ?",
            (start_ts_ms, end_ts_ms),
        ) as cursor:
            row = await cursor.fetchone()
        async with conn.execute(
            "SELECT COUNT(*) FROM runtime_events WHERE event_type='VETO_BLOCK' AND created_ts_ms >= ? AND created_ts_ms < ?",
            (start_ts_ms, end_ts_ms),
        ) as cursor:
            veto_row = await cursor.fetchone()
        now = now_ms() if now_ts_ms is None else int(now_ts_ms)
        async with conn.execute(
            "SELECT grade, COUNT(*) FROM signals WHERE lifecycle_state=? AND expiry_ts_ms>? GROUP BY grade",
            (SignalLifecycleState.PUBLISHED.value, now),
        ) as cursor:
            open_by_grade = {str(item[0]): int(item[1]) for item in await cursor.fetchall()}
        async with conn.execute(
            "SELECT COUNT(*) FROM signals WHERE lifecycle_state=? AND expiry_ts_ms>?",
            (SignalLifecycleState.PUBLISHED.value, now),
        ) as cursor:
            open_row = await cursor.fetchone()
        return {
            "signals_total": int(row[0] or 0),
            "signals_by_strategy": await grouped("SELECT strategy_source,COUNT(*) FROM signals WHERE created_ts_ms >= ? AND created_ts_ms < ? GROUP BY strategy_source"),
            "vetoes_total": int(veto_row[0] or 0),
            "vetoes_by_guard": await grouped("SELECT guard_name,COUNT(*) FROM runtime_events WHERE event_type='VETO_BLOCK' AND created_ts_ms >= ? AND created_ts_ms < ? GROUP BY guard_name"),
            "open_signals": int(open_row[0] or 0),
            "open_by_grade": open_by_grade,
        }

    async def get_recent_signal_rows(self, *, limit: int = 10) -> list[SignalRow]:
        conn = self._require_conn()
        bounded = max(1, min(int(limit), 100))
        async with conn.execute(
            "SELECT signal_id, created_ts_ms, symbol, direction, grade, confidence, strategy_source, "
            "entry_low, entry_high, stop_loss, tp1, tp2, tp3, tp4, rr_tp2, expiry_ts_ms, "
            "why_lines_json, veto_state, veto_reason, advisory_warning, size_units_advisory, "
            "notional_usd_advisory, meta_json, lifecycle_state FROM signals "
            "ORDER BY created_ts_ms DESC, rowid DESC LIMIT ?", (bounded,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_signal_row(row) for row in rows]

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
