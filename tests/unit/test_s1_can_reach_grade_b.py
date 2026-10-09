from __future__ import annotations

from app.core.models import CandidateSignal, ChannelName, Direction
from app.risk.consensus import assign_grade, compute_effective_votes
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep


def test_genuinely_strong_s1_setup_reaches_grade_b():
    breakdown = S1LiquiditySweep._confidence_breakdown(
        direction=Direction.LONG,
        base_confidence=0.62,
        taker_buy_ratio=0.60,
        sweep_distance=0.50,
        reclaim_distance=0.30,
        reclaim_band_atr=0.15,
        penetration_min_atr=0.25,
        volume_ratio=1.5,
    )
    candidate = CandidateSignal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        strategy_source="S1",
        confidence=breakdown["final"],
        channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
        entry_low=100.0,
        entry_high=100.5,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        tp3=103.0,
        tp4=105.0,
        why_lines=("strong S1 setup",),
        meta={"confidence_breakdown": breakdown},
        event_ts_ms=1_000,
    )

    result = compute_effective_votes([candidate])
    grade = assign_grade(result, {"grade_b_min_conf": 0.55,
                                  "grade_b_min_veff": 1.0,
                                  "grade_a_min_conf": 0.66,
                                  "grade_a_min_veff": 1.75,
                                  "grade_a_min_groups": 2,
                                  "grade_a_plus_min_conf": 0.82,
                                  "grade_a_plus_min_veff": 2.5,
                                  "grade_a_plus_min_groups": 3})

    assert candidate.confidence > 0.55
    assert grade == "B"
