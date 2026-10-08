"""Database row models.

These are plain dataclasses representing rows as persisted to SQLite.
They are deliberately separate from app.signals.models.Signal (the
domain/pydantic model) so that persistence concerns (column types,
JSON-encoding of nested structures) don't leak into the domain model,
and so the domain model can evolve without forcing a migration for
every field change (the repository layer handles the mapping).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalRow:
    signal_id: str
    created_ts_ms: int
    symbol: str
    direction: str
    grade: str
    confidence: float
    strategy_source: str
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    rr_tp2: float
    expiry_ts_ms: int
    why_lines_json: str
    veto_state: str
    veto_reason: str | None
    advisory_warning: str
    size_units_advisory: float
    notional_usd_advisory: float
    meta_json: str
    lifecycle_state: str


@dataclass(frozen=True)
class LifecycleEventRow:
    id: int | None
    signal_id: str
    from_state: str
    to_state: str
    ts_ms: int
    reason: str | None


@dataclass(frozen=True)
class CandidateAuditRow:
    """Every candidate a strategy produces is logged here regardless
    of whether it survives consensus/veto — required for auditability
    (spec section 2 item 8) and for shadow-mode threshold recalibration
    (KNOWN_UNCERTAINTIES.md item 1)."""

    id: int | None
    symbol: str
    strategy_source: str
    direction: str
    confidence: float
    event_ts_ms: int
    snapshot_version: str
    meta_json: str
    created_ts_ms: int
