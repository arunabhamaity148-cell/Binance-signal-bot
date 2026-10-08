"""Soak-run observation and elapsed-wall-clock completion semantics."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

REQUIRED_SOAK_SECONDS=72*60*60
EVENT_KINDS=frozenset({"feed_stability","stale_data","signal_why_latency","veto_rate","duplicate",
                       "exception","cpu_rss","restart_recovery"})

@dataclass(frozen=True)
class SoakEvent:
    kind: str
    timestamp: datetime
    details: dict = field(default_factory=dict)

class SoakMonitor:
    """Collect events and report PENDING until 72 actual wall-clock hours elapsed.

    `test_run=True` explicitly marks a short observer/test; such a run cannot
    report COMPLETED even if its clock is advanced artificially.
    """
    def __init__(self, *, started_at: datetime | None=None, test_run: bool=False) -> None:
        self.started_at=self._utc(started_at or datetime.now(timezone.utc))
        self.test_run=bool(test_run)
        self.events: list[SoakEvent]=[]

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def record(self, kind: str, details: dict | None=None, *, timestamp: datetime | None=None) -> SoakEvent:
        if kind not in EVENT_KINDS: raise ValueError(f"unsupported soak event kind: {kind}")
        event=SoakEvent(kind,self._utc(timestamp or datetime.now(timezone.utc)),dict(details or {}))
        self.events.append(event); return event

    def record_feed_stability(self, **details): return self.record("feed_stability",details)
    def record_stale_data(self, **details): return self.record("stale_data",details)
    def record_signal_why_latency(self, **details): return self.record("signal_why_latency",details)
    def record_veto_rate(self, **details): return self.record("veto_rate",details)
    def record_duplicate(self, **details): return self.record("duplicate",details)
    def record_exception(self, **details): return self.record("exception",details)
    def record_cpu_rss(self, **details): return self.record("cpu_rss",details)
    def record_restart_recovery(self, **details): return self.record("restart_recovery",details)

    def status(self, *, now: datetime | None=None) -> str:
        current=self._utc(now or datetime.now(timezone.utc))
        elapsed=max(0.0,(current-self.started_at).total_seconds())
        if self.test_run or elapsed < REQUIRED_SOAK_SECONDS: return "PENDING"
        return "COMPLETED"

    def report(self, *, now: datetime | None=None) -> dict:
        current=self._utc(now or datetime.now(timezone.utc))
        elapsed=max(0.0,(current-self.started_at).total_seconds())
        return {"status":self.status(now=current),"run_kind":"TEST RUN" if self.test_run else "REAL SOAK",
                "started_at_utc":self.started_at.isoformat(),"elapsed_seconds":elapsed,
                "required_seconds":REQUIRED_SOAK_SECONDS,"event_count":len(self.events),
                "events_by_kind":dict(Counter(event.kind for event in self.events))}
