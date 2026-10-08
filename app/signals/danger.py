"""Post-publish invalidation ("danger state") tracking.

A published Signal's thesis can be invalidated by market data that
arrives after publication (e.g. S1's invalidation condition: close
fully reverses through the opposite swing extreme). This module
re-checks a published signal's invalidation condition against a fresh
MarketSnapshot and reports whether the thesis is now considered dead.

This is deliberately generic (strategy-agnostic) at the top level: it
delegates the actual invalidation formula to a per-strategy checker
function supplied by the caller, since each strategy's invalidation
condition is different (see STRATEGIES_SPEC.md, "Invalidation" for
each of S1-S5). Batch 2 wires this for S1 and S4 only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.models import Direction, MarketSnapshot


@dataclass(frozen=True)
class DangerAssessment:
    signal_id: str
    is_invalidated: bool
    reason: str | None


def assess_s1_invalidation(
    signal_id: str, direction: Direction, l_high: float | None, l_low: float | None, snapshot: MarketSnapshot
) -> DangerAssessment:
    """S1 invalidation: close fully reverses through the opposite swing
    extreme (close[i+1] < L_low for LONG, close[i+1] > L_high for SHORT)."""
    bars = snapshot.klines_for("5m")
    if not bars:
        return DangerAssessment(signal_id=signal_id, is_invalidated=False, reason=None)
    latest_close = bars[-1].close

    if direction == Direction.LONG:
        if l_low is not None and latest_close < l_low:
            return DangerAssessment(
                signal_id=signal_id, is_invalidated=True,
                reason=f"close {latest_close} reversed below L_low {l_low}",
            )
    else:
        if l_high is not None and latest_close > l_high:
            return DangerAssessment(
                signal_id=signal_id, is_invalidated=True,
                reason=f"close {latest_close} reversed above L_high {l_high}",
            )
    return DangerAssessment(signal_id=signal_id, is_invalidated=False, reason=None)


def assess_s4_invalidation(
    signal_id: str, direction: Direction, ema_slow: float, snapshot: MarketSnapshot
) -> DangerAssessment:
    """S4 invalidation: close beyond ema_slow (trend structurally
    broken). The 'OI contraction' half of the spec's OR condition is
    intentionally checked by the caller with fresh OI data; this
    function covers the always-computable price-based half."""
    bars = snapshot.klines_for("1h")
    if not bars:
        return DangerAssessment(signal_id=signal_id, is_invalidated=False, reason=None)
    latest_close = bars[-1].close

    if direction == Direction.LONG and latest_close < ema_slow:
        return DangerAssessment(
            signal_id=signal_id, is_invalidated=True,
            reason=f"close {latest_close} broke below ema_slow {ema_slow}",
        )
    if direction == Direction.SHORT and latest_close > ema_slow:
        return DangerAssessment(
            signal_id=signal_id, is_invalidated=True,
            reason=f"close {latest_close} broke above ema_slow {ema_slow}",
        )
    return DangerAssessment(signal_id=signal_id, is_invalidated=False, reason=None)


CheckerFn = Callable[[str, Direction, MarketSnapshot], DangerAssessment]
