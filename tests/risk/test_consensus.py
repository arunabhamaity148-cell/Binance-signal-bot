from __future__ import annotations

import math

from app.config import load_all
from app.core.models import CandidateSignal, ChannelName, Direction
from app.risk.consensus import assign_grade, compute_effective_votes

CONSENSUS_CFG = load_all().strategy["consensus"]


def _candidate(strategy_source, confidence, channels):
    return CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source=strategy_source,
        confidence=confidence, channels=channels,
        entry_low=100, entry_high=100.5, stop_loss=99, tp1=101, tp2=102, tp3=103, tp4=105,
        why_lines=("r",), meta={}, event_ts_ms=1000,
    )


def test_compute_effective_votes_empty_list():
    result = compute_effective_votes([])
    assert result.v_eff == 0.0
    assert result.confidence_weighted == 0.0
    assert result.num_groups == 0


def test_compute_effective_votes_single_candidate():
    c = _candidate("S1", 0.8, (ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW))
    result = compute_effective_votes([c])
    # num_channels=2, each channel has only this candidate at rank 1
    # w_ic = (1/2)*(1/sqrt(1)) = 0.5 per channel, v_eff = 1.0
    assert math.isclose(result.v_eff, 1.0, rel_tol=1e-9)
    assert math.isclose(result.confidence_weighted, 0.8, rel_tol=1e-9)
    assert result.num_groups == 2


def test_compute_effective_votes_two_candidates_different_channels_higher_veff():
    c1 = _candidate("S1", 0.8, (ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW))
    c2 = _candidate("S4", 0.7, (ChannelName.PRICE_STRUCTURE, ChannelName.OI))
    result = compute_effective_votes([c1, c2])
    # Each candidate uses entirely distinct channels -> both rank 1
    # within their own channels -> v_eff = 1.0 + 1.0 = 2.0
    assert math.isclose(result.v_eff, 2.0, rel_tol=1e-9)
    assert result.num_groups == 4  # 4 distinct channels used


def test_compute_effective_votes_overlapping_channel_reduces_rank_weight():
    """Two candidates sharing a channel: the lower-confidence one gets
    rank 2 in that shared channel, reducing its weight contribution
    relative to being alone."""
    c1 = _candidate("S1", 0.9, (ChannelName.OI,))
    c2 = _candidate("S5", 0.5, (ChannelName.OI,))
    result = compute_effective_votes([c1, c2])
    # c1: rank 1 in OI -> w=1/1 * 1/sqrt(1) = 1.0
    # c2: rank 2 in OI -> w=1/1 * 1/sqrt(2) = 0.707
    assert result.per_candidate_weight[0] == 1.0
    assert math.isclose(result.per_candidate_weight[1], 1.0 / math.sqrt(2), rel_tol=1e-9)


def test_confidence_weighted_is_weighted_average():
    c1 = _candidate("S1", 1.0, (ChannelName.LIQUIDITY,))
    c2 = _candidate("S4", 0.0, (ChannelName.LIQUIDITY,))
    result = compute_effective_votes([c1, c2])
    # c1 rank 1 weight=1.0, c2 rank 2 weight=1/sqrt(2)
    w1, w2 = 1.0, 1.0 / math.sqrt(2)
    expected = (w1 * 1.0 + w2 * 0.0) / (w1 + w2)
    assert math.isclose(result.confidence_weighted, expected, rel_tol=1e-9)


def test_assign_grade_a_plus_thresholds():
    from app.risk.consensus import ConsensusResult

    result = ConsensusResult(
        grade=None, confidence_weighted=0.90, v_eff=3.0, num_groups=4, per_candidate_weight={}
    )
    assert assign_grade(result, CONSENSUS_CFG) == "A+"


def test_assign_grade_a_thresholds():
    from app.risk.consensus import ConsensusResult

    result = ConsensusResult(
        grade=None, confidence_weighted=0.70, v_eff=1.8, num_groups=2, per_candidate_weight={}
    )
    assert assign_grade(result, CONSENSUS_CFG) == "A"


def test_assign_grade_b_thresholds():
    from app.risk.consensus import ConsensusResult

    result = ConsensusResult(
        grade=None, confidence_weighted=0.56, v_eff=1.0, num_groups=1, per_candidate_weight={}
    )
    assert assign_grade(result, CONSENSUS_CFG) == "B"


def test_assign_grade_no_trade_below_all_thresholds():
    from app.risk.consensus import ConsensusResult

    result = ConsensusResult(
        grade=None, confidence_weighted=0.1, v_eff=0.1, num_groups=1, per_candidate_weight={}
    )
    assert assign_grade(result, CONSENSUS_CFG) is None


def test_assign_grade_high_confidence_but_insufficient_groups_falls_to_lower_grade():
    """A+ requires >=3 groups; a candidate set with high confidence and
    v_eff but only 1 group must not receive A+ (spec section 11:
    consensus must not count multiple votes on the same evidence as
    independent confirmation)."""
    from app.risk.consensus import ConsensusResult

    result = ConsensusResult(
        grade=None, confidence_weighted=0.95, v_eff=3.0, num_groups=1, per_candidate_weight={}
    )
    grade = assign_grade(result, CONSENSUS_CFG)
    assert grade != "A+"
