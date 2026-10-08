from __future__ import annotations

import pytest

from app.signals.lifecycle import SignalLifecycleState, can_transition, transition


def test_pending_to_published_valid():
    assert can_transition(SignalLifecycleState.PENDING, SignalLifecycleState.PUBLISHED)


def test_pending_to_invalidated_valid():
    assert can_transition(SignalLifecycleState.PENDING, SignalLifecycleState.INVALIDATED)


def test_pending_to_expired_invalid():
    assert not can_transition(SignalLifecycleState.PENDING, SignalLifecycleState.EXPIRED)


def test_published_to_expired_valid():
    assert can_transition(SignalLifecycleState.PUBLISHED, SignalLifecycleState.EXPIRED)


def test_published_to_filled_advisory_valid():
    assert can_transition(SignalLifecycleState.PUBLISHED, SignalLifecycleState.FILLED_ADVISORY)


def test_expired_is_terminal():
    assert not can_transition(SignalLifecycleState.EXPIRED, SignalLifecycleState.PUBLISHED)
    assert not can_transition(SignalLifecycleState.EXPIRED, SignalLifecycleState.INVALIDATED)


def test_invalidated_is_terminal():
    assert not can_transition(SignalLifecycleState.INVALIDATED, SignalLifecycleState.PUBLISHED)


def test_filled_advisory_can_still_expire_or_invalidate():
    assert can_transition(SignalLifecycleState.FILLED_ADVISORY, SignalLifecycleState.EXPIRED)
    assert can_transition(SignalLifecycleState.FILLED_ADVISORY, SignalLifecycleState.INVALIDATED)


def test_transition_returns_record_on_valid_move():
    t = transition("sig1", SignalLifecycleState.PENDING, SignalLifecycleState.PUBLISHED, ts_ms=1000)
    assert t.signal_id == "sig1"
    assert t.from_state == SignalLifecycleState.PENDING
    assert t.to_state == SignalLifecycleState.PUBLISHED


def test_transition_raises_on_invalid_move():
    with pytest.raises(ValueError):
        transition("sig1", SignalLifecycleState.EXPIRED, SignalLifecycleState.PUBLISHED, ts_ms=1000)


def test_transition_carries_reason():
    t = transition(
        "sig1", SignalLifecycleState.PUBLISHED, SignalLifecycleState.INVALIDATED,
        ts_ms=1000, reason="structure broke",
    )
    assert t.reason == "structure broke"
