from __future__ import annotations

import httpx
import pytest

from app.database.repository import SignalRepository
from app.signals.lifecycle import SignalLifecycleState
from app.telegram.queue import TelegramQueue, TelegramLimitsConfig
from app.telegram.sender import SendOutcome, TelegramCredentials, TelegramSender
from tests.unit.test_repository import _sample_signal
from tests.integration.test_telegram_format import _ctx, _signal


def _limits():
    return TelegramLimitsConfig(100.0, 1000, 1024)


@pytest.mark.asyncio
async def test_sender_requires_telegram_ok_true_and_returns_message_id():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 987}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(TelegramCredentials("fake", "123", False), TelegramQueue(_limits()), client=client)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.SENT
        assert result.telegram_ok is True
        assert result.http_status == 200
        assert result.message_id == "987"
        assert result.dry_run is False
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_distinguishes_telegram_ok_false():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(TelegramCredentials("fake", "123", False), TelegramQueue(_limits()), client=client)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.TELEGRAM_REJECTED
        assert result.telegram_ok is False
        assert result.http_status == 200
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_repository_persists_delivery_operator_event_and_separate_expiry(tmp_path):
    repo = SignalRepository(tmp_path / "phase2.db")
    await repo.connect()
    try:
        now = 10_000
        signal = _sample_signal("CSB-20261012-PHASE2").model_copy(
            update={"created_ts_ms": now, "expiry_ts_ms": now + 1_000,
                    "entry_expiry_ts_ms": now + 500, "management_horizon_minutes": 120,
                    "time_stop_minutes": 60}
        )
        await repo.insert_signal(signal)
        await repo.record_telegram_delivery(
            signal_id=signal.signal_id, attempted_ts_ms=now + 1,
            outcome="SENT", dry_run=False, http_status=200,
            telegram_ok=True, message_id="987", detail="Telegram API ok=true",
        )
        await repo.record_operator_event(
            signal_id=signal.signal_id, event_type="FILLED_REPORTED",
            event_ts_ms=now + 200, price=100.25, note="operator report",
        )
        await repo.update_lifecycle_state(signal.signal_id, "PENDING", "PUBLISHED", now + 2)
        assert await repo.advance_expiry(now_ts_ms=now + 600) == 1
        row = await repo.get_signal_by_id(signal.signal_id)
        assert row is not None and row.lifecycle_state == SignalLifecycleState.ENTRY_WINDOW_EXPIRED.value
        assert await repo.advance_expiry(now_ts_ms=now + 1_100) == 1
        row = await repo.get_signal_by_id(signal.signal_id)
        assert row is not None and row.lifecycle_state == SignalLifecycleState.EXPIRED.value
        async with repo._conn.execute("SELECT outcome,telegram_ok,message_id FROM telegram_delivery_events") as cur:
            assert await cur.fetchone() == ("SENT", 1, "987")
        async with repo._conn.execute("SELECT event_type,price,provenance FROM operator_signal_events") as cur:
            assert await cur.fetchone() == ("FILLED_REPORTED", 100.25, "manual")
        async with repo._conn.execute("SELECT entry_expiry_ts_ms,management_expiry_ts_ms FROM signal_timing") as cur:
            assert await cur.fetchone() == (now + 500, now + 1_000)
    finally:
        await repo.close()
