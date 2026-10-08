from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import pytest

from app.database.repository import SignalRepository
from app.telegram.error_notifier import ErrorNotifier, format_error_notification
from app.telegram.queue import TelegramLimitsConfig


@pytest.mark.asyncio
async def test_persists_every_error_but_throttles_telegram_to_one_per_five_minutes(tmp_path):
    repo = SignalRepository(tmp_path / "errors.db")
    await repo.connect()
    now = [0.0]
    sent = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    limits = TelegramLimitsConfig(messages_per_sec_per_chat=1_000_000,
                                  messages_per_min_per_group=1000, max_message_chars=1024)
    notifier = ErrorNotifier(repo, bot_token="TEST_TOKEN", chat_id="errors-only-chat",
                             dry_run=False, telegram_limits=limits, client=client,
                             monotonic_fn=lambda: now[0])
    await notifier.start()
    try:
        for stamp in (0.0, 60.0, 301.0):
            now[0] = stamp
            record = logging.LogRecord("app.bot", logging.ERROR, "/tmp/bot.py", 21,
                                       "evaluation failed", (), None)
            record.created = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp() + stamp
            record.context = {"symbol": "BTCUSDT", "strategy": "S3"}
            notifier.handle(record)
        await asyncio.sleep(0)
        await notifier.drain()
        assert len(sent) == 2
        assert notifier.stats["enqueued"] == 3
        assert notifier.stats["rate_limited"] == 1
        assert all("errors-only-chat" in str(call.content) for call in sent)
        report_data = await repo.get_daily_report_data(start_ts_ms=0, end_ts_ms=10_000_000_000_000)
        assert report_data["errors_by_severity"] == {"ERROR": 3}
    finally:
        await notifier.stop()
        await repo.close()


def test_error_message_has_ist_timestamp_three_frames_and_event_id():
    timestamp = int(datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    from app.telegram.error_notifier import _ErrorRecord
    item = _ErrorRecord(timestamp, "CRITICAL", "app.main", "RuntimeError", "startup failed",
                        "symbol=BTCUSDT, strategy=S1",
                        ("a.py:1 in one", "b.py:2 in two", "c.py:3 in three"), True)
    message = format_error_notification(item)
    assert "🚨 ERROR | 2026-10-08 17:30:00 IST" in message
    assert "📍 Source: app.main" in message
    assert "❌ Type: RuntimeError" in message
    assert "💬 Message: startup failed" in message
    assert "📎 Context: symbol=BTCUSDT, strategy=S1" in message
    assert "a.py:1 in one" in message and "c.py:3 in three" in message
    assert "⏱️ Rate limit: 1 error / 5 min" in message
    assert "ID: ERR-20261008-173000" in message


@pytest.mark.asyncio
async def test_notifier_dry_run_persists_without_network(tmp_path):
    repo = SignalRepository(tmp_path / "dry.db")
    await repo.connect()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("dry run must not contact Telegram")))
    notifier = ErrorNotifier(repo, bot_token="fake", chat_id="errors", dry_run=True,
                             telegram_limits=TelegramLimitsConfig(10, 100, 1024), client=client)
    await notifier.start()
    try:
        record = logging.LogRecord("app.test", logging.CRITICAL, "test.py", 1, "critical issue", (), None)
        notifier.handle(record)
        await notifier.drain()
        data = await repo.get_daily_report_data(start_ts_ms=0, end_ts_ms=10_000_000_000_000)
        assert data["errors_by_severity"] == {"CRITICAL": 1}
    finally:
        await notifier.stop()
        await repo.close()


@pytest.mark.asyncio
async def test_telegram_failure_is_contained_and_event_remains_persisted(tmp_path, capsys):
    repo = SignalRepository(tmp_path / "failure.db")
    await repo.connect()

    def fail_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated Telegram outage", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail_transport))
    notifier = ErrorNotifier(repo, bot_token="fake-token", chat_id="errors", dry_run=False,
                             telegram_limits=TelegramLimitsConfig(1_000_000, 1000, 1024), client=client)
    await notifier.start()
    try:
        record = logging.LogRecord("app.test", logging.ERROR, "test.py", 3, "loop recovered", (), None)
        notifier.handle(record)
        await asyncio.sleep(0)
        await notifier.drain()
        assert "Telegram error notification failed" in capsys.readouterr().err
        data = await repo.get_daily_report_data(start_ts_ms=0, end_ts_ms=10**15)
        assert data["errors_by_severity"] == {"ERROR": 1}
    finally:
        await notifier.stop()
        await repo.close()
