from __future__ import annotations

from app.core.models import CandidateSignal, Direction
from app.risk.risk_engine import CandidateDeduplicator


def _candidate(confidence: float) -> CandidateSignal:
    return CandidateSignal(
        symbol="OPUSDT", direction=Direction.SHORT, strategy_source="S1", confidence=confidence,
        channels=(), entry_low=100.0, entry_high=100.0, stop_loss=101.0,
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=95.0, why_lines=("synthetic",), meta={}, event_ts_ms=1,
    )


def test_identical_candidate_within_five_minutes_is_suppressed():
    dedup = CandidateDeduplicator()
    candidate = _candidate(0.5437)
    assert not dedup.should_suppress(candidate, now_ts_ms=1_000_000, cooldown_min=30)
    assert dedup.should_suppress(candidate, now_ts_ms=1_000_000 + 5 * 60_000, cooldown_min=30)


def test_significantly_different_confidence_within_window_is_kept():
    dedup = CandidateDeduplicator()
    assert not dedup.should_suppress(_candidate(0.5437), now_ts_ms=1_000_000, cooldown_min=30)
    assert not dedup.should_suppress(_candidate(0.5500), now_ts_ms=1_000_000 + 5 * 60_000, cooldown_min=30)


def test_identical_candidate_after_cooldown_is_kept():
    dedup = CandidateDeduplicator()
    assert not dedup.should_suppress(_candidate(0.5437), now_ts_ms=1_000_000, cooldown_min=30)
    assert not dedup.should_suppress(_candidate(0.5437), now_ts_ms=1_000_000 + 30 * 60_000, cooldown_min=30)
