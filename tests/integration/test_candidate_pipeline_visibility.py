from __future__ import annotations

import asyncio
import logging

import pytest

from app.bot import SignalBot
from app.config import load_all
from tests.integration.test_pipeline_e2e import _news
from tests.integration.test_signal_pipeline_live_path import (
    _ClosedRest,
    _RecordingSender,
    _tradable_snapshot,
)


@pytest.mark.asyncio
async def test_s1_candidate_pending_and_created_are_visible_at_info(caplog, tmp_path):
    cfg = load_all()
    from app.database.repository import SignalRepository

    repo = SignalRepository(tmp_path / "visibility.db")
    await repo.connect()
    sender = _RecordingSender()
    bot = SignalBot(cfg, 1000.0, rest_client=_ClosedRest(), repository=repo, sender=sender)
    bot.symbols = ["BTCUSDT"]
    bot.outbox_task = asyncio.create_task(bot._publisher_loop())
    try:
        with caplog.at_level(logging.INFO):
            await bot.evaluate_symbol(_tradable_snapshot(), _news(_tradable_snapshot()))
        pending = [r for r in caplog.records if r.getMessage() == "s1_candidate_pending"]
        created = [r for r in caplog.records if r.getMessage() == "candidate_created"]
        evaluations = [r for r in caplog.records if r.getMessage() == "strategy_eval"]
        assert pending and pending[0].context["confidence"] > 0.55
        assert created and created[0].context["stage"] == "created"
        assert evaluations and evaluations[0].context["s1_cand"] == 1
    finally:
        await bot.shutdown()
