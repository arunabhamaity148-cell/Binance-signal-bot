from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.config import load_and_validate_all
from app.core.time_utils import now_ms
from app.telegram.commands import CommandContext, dispatch_command, format_feed_lag


def _context(state):
    return CommandContext(
        repository=object(),
        config=load_and_validate_all(),
        allowed_chat_ids=frozenset({"123"}),
        state_provider=lambda: state,
    )


def test_status_uses_latest_ws_receive_timestamp_not_historical_age():
    stamp = now_ms() - 250
    ws = SimpleNamespace(last_message_ts_ms=stamp)
    message = asyncio.run(dispatch_command("/status", chat_id="123", context=_context({"ws": ws, "ws_status": "connected"})))
    assert message is not None
    assert "Feed lag:" in message
    assert "ms" in message
    assert "day" not in message
    assert "2875401040" not in message


def test_status_reports_na_when_ws_timestamp_is_unavailable():
    message = asyncio.run(dispatch_command("/status", chat_id="123", context=_context({"ws_status": "connected"})))
    assert message is not None
    assert "Feed lag: ⚪ N/A" in message


def test_feed_lag_threshold_colors_are_human_readable():
    assert format_feed_lag(9_999_750, now_ts_ms=10_000_000) == "🟢 250ms"
    assert format_feed_lag(9_997_700, now_ts_ms=10_000_000) == "🟡 2.3s"
    assert format_feed_lag(9_988_000, now_ts_ms=10_000_000) == "🔴 12.0s"
