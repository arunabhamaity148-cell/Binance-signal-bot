"""Signal lifecycle state machine.

Tracks a published signal's own state over time: PENDING (constructed,
not yet sent) -> PUBLISHED (sent to Telegram) -> one of EXPIRED,
INVALIDATED, or FILLED_ADVISORY (a human reported acting on it, purely
informational since this bot never observes real fills).

This is distinct from `danger.py`, which tracks whether a published
signal's underlying thesis has been invalidated by subsequent market
data (a separate concern: lifecycle is about the signal's own
administrative state, danger is about whether its thesis still holds).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalLifecycleState(str, Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    FILLED_ADVISORY = "FILLED_ADVISORY"


_VALID_TRANSITIONS: dict[SignalLifecycleState, set[SignalLifecycleState]] = {
    SignalLifecycleState.PENDING: {SignalLifecycleState.PUBLISHED, SignalLifecycleState.INVALIDATED},
    SignalLifecycleState.PUBLISHED: {
        SignalLifecycleState.EXPIRED,
        SignalLifecycleState.INVALIDATED,
        SignalLifecycleState.FILLED_ADVISORY,
    },
    SignalLifecycleState.EXPIRED: set(),
    SignalLifecycleState.INVALIDATED: set(),
    SignalLifecycleState.FILLED_ADVISORY: {SignalLifecycleState.EXPIRED, SignalLifecycleState.INVALIDATED},
}


@dataclass(frozen=True)
class LifecycleTransition:
    signal_id: str
    from_state: SignalLifecycleState
    to_state: SignalLifecycleState
    ts_ms: int
    reason: str | None = None


def can_transition(from_state: SignalLifecycleState, to_state: SignalLifecycleState) -> bool:
    return to_state in _VALID_TRANSITIONS.get(from_state, set())


def transition(
    signal_id: str, from_state: SignalLifecycleState, to_state: SignalLifecycleState, ts_ms: int, reason: str | None = None
) -> LifecycleTransition:
    if not can_transition(from_state, to_state):
        raise ValueError(f"invalid lifecycle transition for {signal_id}: {from_state} -> {to_state}")
    return LifecycleTransition(
        signal_id=signal_id, from_state=from_state, to_state=to_state, ts_ms=ts_ms, reason=reason
    )
