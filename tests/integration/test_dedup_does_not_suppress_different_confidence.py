from __future__ import annotations

from app.core.models import CandidateSignal, ChannelName, Direction
from app.risk.consensus import compute_effective_votes
from app.risk.risk_engine import CandidateDeduplicator


def _candidate(confidence: float) -> CandidateSignal:
    return CandidateSignal(
        symbol="DOTUSDT", direction=Direction.LONG, strategy_source="S1", confidence=confidence,
        channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
        entry_low=100.0, entry_high=100.0, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0,
        why_lines=("synthetic",), meta={}, event_ts_ms=1,
    )


def test_two_materially_different_confidences_reach_consensus():
    dedup = CandidateDeduplicator()
    first = _candidate(0.69)
    second = _candidate(0.65)
    assert not dedup.should_suppress(first, now_ts_ms=1_000_000, cooldown_min=30)
    assert not dedup.should_suppress(second, now_ts_ms=1_000_001, cooldown_min=30)
    result = compute_effective_votes([first, second])
    assert result.v_eff > 0
