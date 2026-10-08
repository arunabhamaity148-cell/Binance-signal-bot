"""Effective-vote consensus grading (spec section 11).

Implements the exact formula from spec section 11:

    w_{i,c} = (1 / |C_i|) * (1 / sqrt(rank_{i,c}))
    V_eff = sum_i ( sum_{c in C_i} w_{i,c} )
    confidence_weighted = sum_i (w_i * c_i) / sum_i (w_i)

where `w_i` (a candidate's total weight, used in the weighted-confidence
formula) is taken as the sum of that candidate's per-channel weights,
`sum_{c in C_i} w_{i,c}`.

Grade thresholds are read from config/strategy.yaml's `consensus`
section — all currently class F (unvalidated), per
KNOWN_UNCERTAINTIES.md item 2. This module does not alter that
classification; it just applies the configured numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.models import CandidateSignal, ChannelName, Grade


@dataclass(frozen=True)
class ConsensusResult:
    grade: str | None  # "A+" | "A" | "B" | None (None = NO TRADE)
    confidence_weighted: float
    v_eff: float
    num_groups: int
    per_candidate_weight: dict[int, float]  # index into the input list -> weight


def _rank_within_channel(
    candidates: list[CandidateSignal], channel: ChannelName
) -> dict[int, int]:
    """1-based rank (by descending confidence) of each candidate that
    uses `channel`, among only the candidates that use that channel."""
    indices_using_channel = [i for i, c in enumerate(candidates) if channel in c.channels]
    sorted_by_confidence = sorted(indices_using_channel, key=lambda i: candidates[i].confidence, reverse=True)
    return {idx: rank + 1 for rank, idx in enumerate(sorted_by_confidence)}


def compute_effective_votes(candidates: list[CandidateSignal]) -> ConsensusResult:
    """Compute V_eff, weighted confidence, and distinct-group count for
    a set of same-direction, same-symbol candidates (callers are
    responsible for grouping by symbol+direction before calling this —
    consensus is meaningless across conflicting directions).

    Returns grade=None (meaning the candidate set, at raw confidence,
    has zero effective votes) only when `candidates` is empty; grade
    assignment against thresholds happens in `assign_grade`, kept
    separate so this function stays a pure vote-counting primitive.
    """
    if not candidates:
        return ConsensusResult(grade=None, confidence_weighted=0.0, v_eff=0.0, num_groups=0, per_candidate_weight={})

    all_channels_used: set[ChannelName] = set()
    for c in candidates:
        all_channels_used.update(c.channels)

    rank_by_channel: dict[ChannelName, dict[int, int]] = {
        channel: _rank_within_channel(candidates, channel) for channel in all_channels_used
    }

    per_candidate_weight: dict[int, float] = {}
    v_eff = 0.0
    for i, candidate in enumerate(candidates):
        num_channels = len(candidate.channels)
        if num_channels == 0:
            per_candidate_weight[i] = 0.0
            continue
        weight_sum = 0.0
        for channel in candidate.channels:
            rank = rank_by_channel[channel][i]
            w_ic = (1.0 / num_channels) * (1.0 / math.sqrt(rank))
            weight_sum += w_ic
            v_eff += w_ic
        per_candidate_weight[i] = weight_sum

    total_weight = sum(per_candidate_weight.values())
    if total_weight > 0:
        confidence_weighted = (
            sum(per_candidate_weight[i] * candidates[i].confidence for i in per_candidate_weight)
            / total_weight
        )
    else:
        confidence_weighted = 0.0

    num_groups = len(all_channels_used)

    return ConsensusResult(
        grade=None,
        confidence_weighted=confidence_weighted,
        v_eff=v_eff,
        num_groups=num_groups,
        per_candidate_weight=per_candidate_weight,
    )


def assign_grade(result: ConsensusResult, consensus_cfg: dict) -> str | None:
    """Apply the configured grade thresholds to a ConsensusResult.
    Returns "A+"/"A"/"B", or None for NO TRADE. Thresholds are class F
    (unvalidated) per config/strategy.yaml.
    """
    if (
        result.confidence_weighted >= consensus_cfg["grade_a_plus_min_conf"]
        and result.v_eff >= consensus_cfg["grade_a_plus_min_veff"]
        and result.num_groups >= consensus_cfg["grade_a_plus_min_groups"]
    ):
        return Grade.A_PLUS.value

    if (
        result.confidence_weighted >= consensus_cfg["grade_a_min_conf"]
        and result.v_eff >= consensus_cfg["grade_a_min_veff"]
        and result.num_groups >= consensus_cfg["grade_a_min_groups"]
    ):
        return Grade.A.value

    if (
        result.confidence_weighted >= consensus_cfg["grade_b_min_conf"]
        and result.v_eff >= consensus_cfg["grade_b_min_veff"]
    ):
        return Grade.B.value

    return None
