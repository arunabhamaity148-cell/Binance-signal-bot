from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import load_and_validate_all
from app.core.models import (CandidateSignal, ChannelName, Direction, GuardAction,
                             GuardResult, GuardSeverity, VetoOutcome, VetoState)
from app.bot import SignalBot


class CaptureRepository:
    def __init__(self):
        self.candidates = []
        self.veto_blocks = []

    async def insert_candidate_audit(self, **kwargs):
        self.candidates.append(kwargs)

    async def record_veto_block(self, **kwargs):
        self.veto_blocks.append(kwargs)


def _candidate():
    return CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S3", confidence=0.9,
        channels=(ChannelName.OI,), entry_low=100.0, entry_high=101.0,
        stop_loss=99.0, tp1=102.0, tp2=103.0, tp3=104.0, tp4=105.0,
        why_lines=("unit test candidate",), meta={}, event_ts_ms=10_000,
    )


@pytest.mark.asyncio
async def test_only_actual_blocking_guard_results_are_persisted(monkeypatch):
    cfg = load_and_validate_all()
    repo = CaptureRepository()
    bot = SignalBot(cfg, 2400.0, rest_client=object(), repository=repo)
    candidate = _candidate()
    monkeypatch.setattr("app.bot.generate_candidates_at_snapshot", lambda *_: [candidate])
    monkeypatch.setattr("app.bot.grade_candidates", lambda *_: {
        ("BTCUSDT", Direction.LONG): ("A", 0.9, [candidate])
    })
    guards = (
        GuardResult("G2", False, GuardSeverity.CRITICAL, GuardAction.BLOCK, "feed unhealthy"),
        GuardResult("G9", False, GuardSeverity.LOW, GuardAction.DEGRADE, "degraded only"),
    )
    monkeypatch.setattr("app.bot.run_veto_engine", lambda **_: VetoOutcome(
        VetoState.BLOCK, "G2 blocked", None, guards
    ))

    await bot.evaluate_symbol(SimpleNamespace(as_of_ts_ms=12_345, snapshot_version="test-v1"), SimpleNamespace())

    assert len(repo.veto_blocks) == 1
    assert repo.veto_blocks[0] == {
        "guard_name": "G2", "symbol": "BTCUSDT", "strategy_source": "S3",
        "event_ts_ms": 12_345, "message": "feed unhealthy",
    }
