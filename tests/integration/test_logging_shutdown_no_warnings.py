from __future__ import annotations

import asyncio
import gc
import logging
import warnings

import httpx
import pytest

from app.database.repository import SignalRepository
from app.telegram.error_notifier import ErrorNotifier
from app.telegram.queue import TelegramLimitsConfig


@pytest.mark.asyncio
async def test_logging_shutdown_does_not_create_unawaited_flush_coroutine(tmp_path):
    repository = SignalRepository(tmp_path / "errors.db")
    await repository.connect()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
    )
    notifier = ErrorNotifier(
        repository,
        bot_token="",
        chat_id="",
        dry_run=True,
        telegram_limits=TelegramLimitsConfig(1, 20, 1024),
        client=client,
    )
    await notifier.start()
    shutdown_error = None
    try:
        assert notifier in logging.getLogger().handlers
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            try:
                logging.shutdown()
            except Exception as exc:  # the regression must not escape shutdown
                shutdown_error = exc
            await asyncio.sleep(0)
            gc.collect()
        assert shutdown_error is None
        assert not any(
            issubclass(item.category, RuntimeWarning)
            and "coroutine 'ErrorNotifier.flush' was never awaited" in str(item.message)
            for item in observed
        )
    finally:
        await notifier.stop()
        await repository.close()
