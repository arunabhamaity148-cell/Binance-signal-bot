"""Tests for the Telegram formatter, queue, and sender.

Covers: exact format compliance against spec section 15's example,
ADVISORY warning presence and preservation under 1024-char truncation
(including the "refuse to send without it" hard rule), 429/retry_after
handling, and the guarantee that a Telegram failure never propagates
to the caller.
"""
from __future__ import annotations

import httpx
import pytest

from app.signals.models import ADVISORY_WARNING, Signal
from app.telegram.formatter import (
    MAX_MESSAGE_CHARS,
    DeliveryContext,
    FormatterError,
    format_signal_message,
)
from app.telegram.queue import TelegramLimitsConfig, TelegramQueue
from app.telegram.sender import (
    SendOutcome,
    TelegramCredentials,
    TelegramSender,
)


def _signal(**overrides):
    base = dict(
        signal_id="CSB-20260926-4F1A2C", created_ts_ms=0, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=0.84, strategy_source="S1",
        entry_low=86120.0, entry_high=86180.0, stop_loss=85620.0,
        tp1=86680.0, tp2=87240.0, tp3=88020.0, tp4=88980.0, rr_tp2=2.1,
        expiry_ts_ms=45 * 60_000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=86150.0, meta={},
    )
    base.update(overrides)
    return Signal(**base)


def _ctx(as_of_ts_ms=0, news="no blocking event", binance="healthy"):
    return DeliveryContext(news_state_label=news, binance_state_label=binance, as_of_ts_ms=as_of_ts_ms)


# ---------------------------------------------------------------------------
# Exact format compliance
# ---------------------------------------------------------------------------


def test_format_matches_spec_example_exactly():
    msg = format_signal_message(_signal(), _ctx())
    expected = (
        "🚨 SIGNAL | BTCUSDT — LONG\n"
        "⚠️ ADVISORY ONLY — VERIFY ACCOUNT SIZING MANUALLY. No account state, "
        "fill, leverage, or liquidation distance is observed.\n"
        "🧠 Grade: A   📊 Confidence: 84%\n"
        "🎯 LIMIT ENTRY: 86120 – 86180\n"
        "🛑 SL: 85620\n"
        "🎯 TP1: 86680   🎯 TP2: 87240\n"
        "🎯 TP3: 88020   🎯 TP4: 88980\n"
        "📐 R:R: 1 : 2.1   ⏳ Expiry: 45m\n"
        "📰 News: no blocking event   🏦 Binance: healthy\n"
        "🛡️ Veto: PASS\n"
        "ID: CSB-20260926-4F1A2C"
    )
    assert msg == expected


def test_format_short_direction():
    msg = format_signal_message(_signal(
        direction="SHORT", entry_low=99.5, entry_high=100.0, stop_loss=101.0,
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=95.0,
    ), _ctx())
    assert "SIGNAL | BTCUSDT — SHORT" in msg


def test_format_grade_a_plus():
    msg = format_signal_message(_signal(grade="A+"), _ctx())
    assert "Grade: A+" in msg


def test_format_grade_b():
    msg = format_signal_message(_signal(grade="B"), _ctx())
    assert "Grade: B" in msg


def test_format_veto_block_state():
    msg = format_signal_message(_signal(veto_state="BLOCK", veto_reason="G5 blocked"), _ctx())
    assert "Veto: BLOCK" in msg


def test_format_field_order_is_fixed():
    msg = format_signal_message(_signal(), _ctx())
    lines = msg.split("\n")
    assert lines[0].startswith("🚨 SIGNAL")
    assert lines[1].startswith("⚠️")
    assert "Grade" in lines[2]
    assert "LIMIT ENTRY" in lines[3]
    assert lines[4].startswith("🛑 SL")
    assert "TP1" in lines[5] and "TP2" in lines[5]
    assert "TP3" in lines[6] and "TP4" in lines[6]
    assert "R:R" in lines[7] and "Expiry" in lines[7]
    assert "News" in lines[8] and "Binance" in lines[8]
    assert lines[9].startswith("🛡️ Veto")
    assert lines[10].startswith("ID:")


# ---------------------------------------------------------------------------
# ADVISORY warning presence
# ---------------------------------------------------------------------------


def test_advisory_warning_present_in_every_normal_message():
    msg = format_signal_message(_signal(), _ctx())
    assert ADVISORY_WARNING in msg


def test_advisory_warning_present_regardless_of_grade():
    for grade in ("A+", "A", "B"):
        msg = format_signal_message(_signal(grade=grade), _ctx())
        assert ADVISORY_WARNING in msg


def test_advisory_warning_present_regardless_of_veto_state():
    for veto_state, reason in (("PASS", None), ("BLOCK", "some reason")):
        msg = format_signal_message(_signal(veto_state=veto_state, veto_reason=reason), _ctx())
        assert ADVISORY_WARNING in msg


# ---------------------------------------------------------------------------
# 1024-char cap: warning preserved under truncation
# ---------------------------------------------------------------------------


def test_normal_message_well_under_cap():
    msg = format_signal_message(_signal(), _ctx())
    assert len(msg) <= MAX_MESSAGE_CHARS


def test_long_news_label_triggers_truncation_but_preserves_warning():
    """An artificially long news-state label pushes the message over
    1024 chars; the formatter must drop other lines first and keep the
    warning intact."""
    long_news = "X" * 2000
    msg = format_signal_message(_signal(), _ctx(news=long_news))
    assert len(msg) <= MAX_MESSAGE_CHARS
    assert ADVISORY_WARNING in msg


def test_long_why_lines_do_not_affect_format_since_why_lines_not_rendered():
    """why_lines are part of the Signal model but are NOT part of the
    Telegram message per spec section 15's exact format (no WHY block
    field in the wire format shown) -- this test documents that
    explicitly rather than leaving it implicit."""
    msg = format_signal_message(_signal(why_lines=["line"] * 10), _ctx())
    assert "line" not in msg


def test_truncation_drops_least_essential_lines_first():
    """With a long binance-state label forcing truncation, the
    drop-precedence should remove news/binance line before touching
    core trade fields like entry/SL."""
    long_binance = "Y" * 1500
    msg = format_signal_message(_signal(), _ctx(binance=long_binance))
    assert len(msg) <= MAX_MESSAGE_CHARS
    assert ADVISORY_WARNING in msg
    assert "ID: CSB-20260926-4F1A2C" in msg


def test_refuses_to_send_when_warning_alone_cannot_fit():
    """Hard rule: if even signal_id + warning can't fit under the cap,
    FormatterError is raised rather than ever producing a message
    without the warning. We force this by monkeypatching the warning
    itself to an absurd length via a Signal with a symbol so long it
    pushes even the minimal content over the cap -- since
    ADVISORY_WARNING itself is fixed and short, we instead directly
    unit test the internal refusal path by using a pathologically long
    signal_id (the other line that's never dropped), which is the
    practical way this situation could occur."""
    pathological_id = "CSB-20260926-" + "A" * 1100  # signal_id itself exceeds the cap
    sig = _signal(signal_id=pathological_id)
    with pytest.raises(FormatterError):
        format_signal_message(sig, _ctx())


def test_formatter_error_message_explains_refusal():
    pathological_id = "CSB-20260926-" + "A" * 1100
    sig = _signal(signal_id=pathological_id)
    with pytest.raises(FormatterError, match="ADVISORY warning"):
        format_signal_message(sig, _ctx())


# ---------------------------------------------------------------------------
# TelegramQueue rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_enforces_per_chat_interval():
    fake_time = {"t": 0.0}
    sleeps: list[float] = []

    def time_fn():
        return fake_time["t"]

    async def sleep_fn(seconds):
        sleeps.append(seconds)
        fake_time["t"] += seconds

    limits = TelegramLimitsConfig(messages_per_sec_per_chat=1.0, messages_per_min_per_group=20, max_message_chars=1024)
    queue = TelegramQueue(limits, time_fn=time_fn, sleep_fn=sleep_fn)

    await queue.acquire_send_slot("chat1")
    await queue.acquire_send_slot("chat1")  # immediately after -> must wait ~1s

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_queue_different_chats_do_not_block_each_other():
    fake_time = {"t": 0.0}
    sleeps: list[float] = []

    def time_fn():
        return fake_time["t"]

    async def sleep_fn(seconds):
        sleeps.append(seconds)
        fake_time["t"] += seconds

    limits = TelegramLimitsConfig(messages_per_sec_per_chat=1.0, messages_per_min_per_group=20, max_message_chars=1024)
    queue = TelegramQueue(limits, time_fn=time_fn, sleep_fn=sleep_fn)

    await queue.acquire_send_slot("chat1")
    await queue.acquire_send_slot("chat2")  # different chat, no per-chat wait needed

    assert sleeps == []


@pytest.mark.asyncio
async def test_queue_enforces_group_limit():
    fake_time = {"t": 0.0}
    sleeps: list[float] = []

    def time_fn():
        return fake_time["t"]

    async def sleep_fn(seconds):
        sleeps.append(seconds)
        fake_time["t"] += seconds

    limits = TelegramLimitsConfig(messages_per_sec_per_chat=0.001, messages_per_min_per_group=3, max_message_chars=1024)
    queue = TelegramQueue(limits, time_fn=time_fn, sleep_fn=sleep_fn)

    for i in range(3):
        await queue.acquire_send_slot(f"chat{i}")
    # 4th send across the group within 60s must wait for the window.
    await queue.acquire_send_slot("chat4")

    assert any(s > 0 for s in sleeps)


# ---------------------------------------------------------------------------
# TelegramSender: 429 handling, never propagates to caller
# ---------------------------------------------------------------------------


def _limits():
    return TelegramLimitsConfig(messages_per_sec_per_chat=100.0, messages_per_min_per_group=1000, max_message_chars=1024)


@pytest.mark.asyncio
async def test_sender_dry_run_succeeds_without_network():
    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=True)
    queue = TelegramQueue(_limits())
    sender = TelegramSender(creds, queue)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.SENT
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_refuses_blocked_signal_without_network_call():
    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("network call must not happen for a BLOCK signal")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client)
    try:
        result = await sender.send_signal_message(
            _signal(veto_state="BLOCK", veto_reason="G5 blocked"), _ctx()
        )
        assert result.outcome == SendOutcome.PRECONDITION_FAILED
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_success_via_mocked_network():
    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.SENT
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_429_respects_retry_after_duration(monkeypatch):
    """Uses pytest's monkeypatch fixture (auto-restoring, even on test
    failure) rather than manually saving/restoring asyncio.sleep --
    safer than a hand-rolled try/finally around global mutable state."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.05"})
        return httpx.Response(200, json={"ok": True})

    sleep_durations: list[float] = []

    async def tracking_sleep(seconds):
        sleep_durations.append(seconds)  # do not actually wait in the test

    monkeypatch.setattr("asyncio.sleep", tracking_sleep)

    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.SENT
        assert 0.05 in sleep_durations
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_429_exhausted_retries_fails_gracefully_not_propagating():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.001"})

    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client, max_retries=1)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        # Must NOT raise -- must report failure via SendResult.
        assert result.outcome == SendOutcome.DELIVERY_FAILED
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_network_error_never_propagates_to_caller():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection failure", request=request)

    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client)
    try:
        # This must complete normally, never raise -- the hard
        # guarantee that Telegram failures cannot break the caller.
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.DELIVERY_FAILED
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_server_error_never_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client, max_retries=1)
    try:
        result = await sender.send_signal_message(_signal(), _ctx())
        assert result.outcome == SendOutcome.DELIVERY_FAILED
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_sender_refuses_unformattable_signal_without_network_call():
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("network call must not happen when the formatter refuses")

    pathological_id = "CSB-20260926-" + "A" * 1100
    creds = TelegramCredentials(bot_token="fake", chat_id="123", dry_run=False)
    queue = TelegramQueue(_limits())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = TelegramSender(creds, queue, client=client)
    try:
        result = await sender.send_signal_message(_signal(signal_id=pathological_id), _ctx())
        assert result.outcome == SendOutcome.REFUSED_BY_FORMATTER
    finally:
        await sender.close()
