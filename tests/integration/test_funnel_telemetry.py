from __future__ import annotations
import asyncio
import json
import logging
import pytest
from app.bot import SignalBot
from app.config import load_all
from app.core.models import VetoState
from app.database.repository import SignalRepository
from app.monitoring import diagnostics
from app.monitoring.metrics import RuntimeMetrics
from app.signals.models import Signal
from app.telegram.formatter import DeliveryContext
from app.telegram.sender import SendOutcome, SendResult
from tests.integration.test_signal_pipeline_live_path import (
    _ClosedRest, _RecordingSender, _signal, _tradable_snapshot,
)
from tests.integration.test_pipeline_e2e import _news


def test_funnel_event_has_stable_stage_and_reason_schema(caplog):
    diagnostics.configure("summary")
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        diagnostics.funnel(
            stage="VETO_BLOCKED", strategy="S3", symbol="BTCUSDT",
            reason_code="veto_blocked", veto_name="G2", created_ts_ms=123,
        )
    event = next(record for record in caplog.records if record.getMessage().startswith("funnel | "))
    payload = json.loads(event.getMessage().split(" | ", 1)[1])
    assert payload["stage"] == "VETO_BLOCKED"
    assert payload["reason_code"] == "veto_blocked"
    assert payload["strategy"] == "S3"
    assert payload["veto_name"] == "G2"


def test_funnel_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown funnel stage"):
        diagnostics.funnel(stage="NOT_A_STAGE")


def test_runtime_funnel_counters_are_aggregated_not_event_storage():
    metrics = RuntimeMetrics()
    metrics.record_funnel(stage="EVALUATED", strategy="S1", reason_code="cycle")
    metrics.record_funnel(stage="PREREQUISITE_REJECTED", strategy="S1", reason_code="insufficient_history", amount=2)
    snapshot = metrics.snapshot()["counters"]["funnel"]
    assert snapshot["EVALUATED"] == 1
    assert snapshot["PREREQUISITE_REJECTED"] == 2
    assert snapshot["by_strategy"]["S1"]["PREREQUISITE_REJECTED"] == 2
    assert snapshot["by_reason"]["insufficient_history"] == 2
    assert "events" not in snapshot


REQUIRED_PHASE1_STAGES = (
    "EVALUATED", "PREREQUISITE_REJECTED", "REGIME_REJECTED", "SETUP_ARMED",
    "SETUP_INVALIDATED", "TRIGGER_MISSED", "TRIGGER_CONFIRMED", "CANDIDATE_CREATED",
    "FINALIZE_REJECTED", "VETO_BLOCKED", "PERSISTED", "QUEUE_ACCEPTED",
    "TELEGRAM_ATTEMPTED", "TELEGRAM_OK", "TELEGRAM_FAILED",
)


def test_every_required_phase1_stage_is_accepted_and_emitted(caplog):
    diagnostics.configure("summary")
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        for stage in REQUIRED_PHASE1_STAGES:
            diagnostics.funnel(stage=stage, strategy="S1", symbol="BTCUSDT", reason_code="fixture")
    events = [r for r in caplog.records if r.getMessage().startswith("funnel | ")]
    assert len(events) == len(REQUIRED_PHASE1_STAGES)
    assert {json.loads(r.getMessage().split(" | ", 1)[1])["stage"] for r in events} == set(REQUIRED_PHASE1_STAGES)


def test_funnel_event_contains_complete_stable_schema_with_nulls(caplog):
    diagnostics.configure("summary")
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        diagnostics.funnel(stage="EVALUATED")
    payload = json.loads(next(r for r in caplog.records if r.getMessage().startswith("funnel | ")).getMessage().split(" | ", 1)[1])
    assert set(payload) == {
        "strategy", "symbol", "direction", "stage", "reason_code", "input_age_ms",
        "regime", "confidence", "veto_name", "threshold", "created_ts_ms",
    }
    assert payload["stage"] == "EVALUATED"
    assert payload["reason_code"] == "unspecified"
    assert payload["strategy"] is None
    assert payload["veto_name"] is None


def test_legacy_reason_labels_are_normalized_to_bounded_reason_codes(caplog):
    diagnostics.configure("summary")
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        diagnostics.funnel(stage="PREREQUISITE_REJECTED", reason_code="if not compression:")
        diagnostics.funnel(stage="PREREQUISITE_REJECTED", reason_code="if len(bars_5m) < required:")
        diagnostics.funnel(stage="PREREQUISITE_REJECTED", reason_code="):")
    payloads = [json.loads(r.getMessage().split(" | ", 1)[1]) for r in caplog.records if r.getMessage().startswith("funnel | ")]
    assert [p["reason_code"] for p in payloads[-3:]] == [
        "range_not_compressed", "insufficient_history", "data_stale_or_unavailable"
    ]


def test_funnel_counters_cover_all_required_stages_without_event_storage():
    metrics = RuntimeMetrics()
    for stage in REQUIRED_PHASE1_STAGES:
        metrics.record_funnel(stage=stage, strategy="S2", reason_code="fixture", amount=2)
    funnel = metrics.snapshot()["counters"]["funnel"]
    for stage in REQUIRED_PHASE1_STAGES:
        assert funnel[stage] == 2
        assert funnel["by_strategy"]["S2"][stage] == 2
    assert funnel["by_reason"]["fixture"] == 2 * len(REQUIRED_PHASE1_STAGES)
    assert "events" not in funnel


def test_funnel_counter_amount_is_integer_and_stage_normalized():
    metrics = RuntimeMetrics()
    metrics.record_funnel(stage="evaluated", strategy="S1", reason_code="cycle", amount=3)
    metrics.record_funnel(stage="EVALUATED", strategy="S1", reason_code="cycle", amount=2)
    snapshot = metrics.snapshot()["counters"]["funnel"]
    assert snapshot["EVALUATED"] == 5
    assert snapshot["by_strategy"]["S1"]["EVALUATED"] == 5
    assert snapshot["by_reason"]["cycle"] == 5


@pytest.mark.asyncio
async def test_evaluate_symbol_increments_candidate_created(tmp_path):
    cfg = load_all()
    repo = SignalRepository(tmp_path / "candidate-created.db")
    sender = _RecordingSender()
    await repo.connect()
    bot = SignalBot(cfg, 1000.0, rest_client=_ClosedRest(), repository=repo, sender=sender)
    bot.outbox_task = asyncio.create_task(bot._publisher_loop())
    snapshot = _tradable_snapshot()
    before = bot.runtime_metrics.snapshot()["counters"]["funnel"].get("CANDIDATE_CREATED", 0)
    try:
        await bot.evaluate_symbol(snapshot, _news(snapshot))
        after = bot.runtime_metrics.snapshot()["counters"]["funnel"].get("CANDIDATE_CREATED", 0)
        assert after == before + 1
    finally:
        await bot.shutdown()


@pytest.mark.asyncio
async def test_evaluate_symbol_increments_veto_blocked(tmp_path, monkeypatch):
    import app.bot as bot_module

    cfg = load_all()
    repo = SignalRepository(tmp_path / "veto-blocked.db")
    sender = _RecordingSender()
    await repo.connect()
    bot = SignalBot(cfg, 1000.0, rest_client=_ClosedRest(), repository=repo, sender=sender)
    bot.outbox_task = asyncio.create_task(bot._publisher_loop())
    snapshot = _tradable_snapshot()

    class Blocked:
        veto_state = VetoState.BLOCK
        veto_reason = "test hard block"
        max_grade_cap = None
        guard_results = []

    monkeypatch.setattr(bot_module, "run_veto_engine", lambda **kwargs: Blocked())
    try:
        await bot.evaluate_symbol(snapshot, _news(snapshot))
        funnel = bot.runtime_metrics.snapshot()["counters"]["funnel"]
        assert funnel.get("VETO_BLOCKED", 0) >= 1
        async with repo._conn.execute("SELECT COUNT(*) FROM signals") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await bot.shutdown()


@pytest.mark.asyncio
async def test_publisher_loop_distinguishes_telegram_ok_and_failed(tmp_path):
    class TwoResponseSender:
        def __init__(self):
            self.responses = [
                SendResult(SendOutcome.SENT, dry_run=False, telegram_ok=True, message_id="m1"),
                SendResult(SendOutcome.TELEGRAM_REJECTED, dry_run=False, telegram_ok=False),
            ]

        async def send_signal_message(self, signal, context):
            return self.responses.pop(0)

        async def close(self):
            return None

    cfg = load_all()
    repo = SignalRepository(tmp_path / "publisher-outcomes.db")
    sender = TwoResponseSender()
    await repo.connect()
    bot = SignalBot(cfg, 1000.0, rest_client=_ClosedRest(), repository=repo, sender=sender)
    first = _signal("CSB-20261012-F1A001", 1_000, 3_600_000)
    second = _signal("CSB-20261012-F1A002", 2_000, 3_601_000)
    await repo.insert_signal(first)
    await repo.insert_signal(second)
    bot.outbox_task = asyncio.create_task(bot._publisher_loop())
    try:
        context = DeliveryContext("clear", "connected", 1_000)
        bot.outbox.put_nowait((first, context))
        await bot.outbox.join()
        bot.outbox.put_nowait((second, context))
        await bot.outbox.join()
        funnel = bot.runtime_metrics.snapshot()["counters"]["funnel"]
        assert funnel.get("TELEGRAM_OK", 0) == 1
        assert funnel.get("TELEGRAM_FAILED", 0) == 1
    finally:
        await bot.shutdown()
