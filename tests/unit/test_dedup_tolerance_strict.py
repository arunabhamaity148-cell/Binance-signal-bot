from __future__ import annotations

from app.core.models import CandidateSignal, Direction
from app.risk.risk_engine import CandidateDeduplicator


def _candidate(confidence: float) -> CandidateSignal:
    return CandidateSignal(
        symbol="DOTUSDT", direction=Direction.LONG, strategy_source="S1", confidence=confidence,
        channels=(), entry_low=100.0, entry_high=100.0, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0,
        why_lines=("synthetic",), meta={}, event_ts_ms=1,
    )


def test_confidence_difference_above_one_e_minus_six_is_not_duplicate():
    dedup = CandidateDeduplicator()
    assert not dedup.should_suppress(_candidate(0.6900000), now_ts_ms=1_000_000, cooldown_min=30)
    assert not dedup.should_suppress(_candidate(0.6900011), now_ts_ms=1_000_001, cooldown_min=30)


def test_confidence_difference_at_or_below_one_e_minus_six_is_duplicate():
    dedup = CandidateDeduplicator()
    assert not dedup.should_suppress(_candidate(0.6900000), now_ts_ms=1_000_000, cooldown_min=30)
    assert dedup.should_suppress(_candidate(0.6900005), now_ts_ms=1_000_001, cooldown_min=30)
    assert dedup.should_suppress(_candidate(0.6900009), now_ts_ms=1_000_002, cooldown_min=30)
