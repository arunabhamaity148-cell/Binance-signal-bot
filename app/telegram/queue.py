"""Telegram delivery queue: enforces per-chat and per-group rate
limits before handing a message to sender.py.

Per spec section 15: "1 msg/sec per chat", "20 msg/min per group".
Values are read from config/system.yaml's telegram_limits (class A,
sourced from Telegram's own documented Bot API limits — not tuned
here).

This module is a pure rate-gate: it decides WHEN a message is allowed
to go out (by chat_id and, separately, by group-wide volume), not HOW
it is sent (that is sender.py's job) or whether it SHOULD be sent at
all (that is the veto_state precondition, enforced by the caller before
a message ever reaches this queue — see sender.py's docstring).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TelegramLimitsConfig:
    messages_per_sec_per_chat: float
    messages_per_min_per_group: int
    max_message_chars: int


def build_telegram_limits(system_cfg: dict) -> TelegramLimitsConfig:
    limits = system_cfg["telegram_limits"]
    return TelegramLimitsConfig(
        messages_per_sec_per_chat=float(limits["messages_per_sec_per_chat"]),
        messages_per_min_per_group=int(limits["messages_per_min_per_group"]),
        max_message_chars=int(limits["max_message_chars"]),
    )


@dataclass
class _ChatRateState:
    last_sent_ts_s: float | None = None


@dataclass
class _GroupRateState:
    """Rolling window of send timestamps for the 20 msg/min group cap."""

    sent_ts_s: deque[float] = field(default_factory=deque)


class TelegramQueue:
    """Rate-gates outgoing messages. `acquire_send_slot` blocks
    (via asyncio.sleep) until it is safe to send to `chat_id`, honoring
    BOTH the per-chat 1 msg/sec limit and the per-group 20 msg/min
    limit (a "group" here is the whole queue instance — if the bot
    posts to multiple distinct Telegram groups, each would get its own
    TelegramQueue instance, consistent with the per-chat/per-group
    distinction in the spec: chat-level pacing is per chat_id, the
    20/min figure is a whole-group ceiling shared across every chat_id
    posting into that group).

    Time source: a monotonic clock function is injected (`time_fn`,
    defaulting to time.monotonic) so tests can control elapsed time
    deterministically rather than sleeping in real wall-clock time.
    """

    def __init__(
        self,
        limits: TelegramLimitsConfig,
        *,
        time_fn=time.monotonic,
        sleep_fn=asyncio.sleep,
    ) -> None:
        self._limits = limits
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._chat_states: dict[str, _ChatRateState] = {}
        self._group_state = _GroupRateState()

    def _chat_state(self, chat_id: str) -> _ChatRateState:
        if chat_id not in self._chat_states:
            self._chat_states[chat_id] = _ChatRateState()
        return self._chat_states[chat_id]

    async def acquire_send_slot(self, chat_id: str) -> None:
        """Blocks until both the per-chat and per-group limits permit a
        send, then records the send as having happened (callers must
        call this immediately before actually sending — this method
        both waits AND records, so there is no separate "record" call
        needed).
        """
        await self._wait_for_chat_slot(chat_id)
        await self._wait_for_group_slot()
        now = self._time_fn()
        self._chat_state(chat_id).last_sent_ts_s = now
        self._group_state.sent_ts_s.append(now)

    async def _wait_for_chat_slot(self, chat_id: str) -> None:
        state = self._chat_state(chat_id)
        if state.last_sent_ts_s is None:
            return
        min_interval_s = 1.0 / self._limits.messages_per_sec_per_chat
        elapsed = self._time_fn() - state.last_sent_ts_s
        if elapsed < min_interval_s:
            await self._sleep_fn(min_interval_s - elapsed)

    async def _wait_for_group_slot(self) -> None:
        """Waits until the group's rolling 60s window has room for
        another send.

        BOUNDARY-SAFETY NOTE (found via a controlled, substitutable time
        source in a Batch 4B regression test): eviction below requires
        an entry to be STRICTLY older than `window_s` (`< cutoff`) to
        leave the window, matching the natural reading of a rolling
        window ("the last 60 seconds"). The wait duration computed to
        let the oldest entry age out must therefore be slightly MORE
        than exactly `window_s`, or the oldest entry lands exactly on
        the cutoff boundary — which fails the strict `<` eviction check
        and never gets evicted, looping forever. On the system clock
        this was effectively never observed (floating-point jitter and
        the clock's own continued advance between the wait calculation
        and the next loop iteration almost always pushed `now` very
        slightly past the boundary by the time it was re-checked), but
        it is a genuine correctness bug, not just a theoretical one — a
        test that substitutes a precisely-controlled, non-advancing
        time source (the injectable `time_fn`/`sleep_fn` this class
        already accepts, used for exactly this kind of deterministic
        check) exposes it reliably as an infinite loop. Fixed with a
        small fixed epsilon added to the computed wait, so the next
        iteration's `now` is always strictly past the cutoff for the
        formerly-oldest entry.
        """
        window_s = 60.0
        epsilon_s = 0.001
        while True:
            now = self._time_fn()
            cutoff = now - window_s
            while self._group_state.sent_ts_s and self._group_state.sent_ts_s[0] < cutoff:
                self._group_state.sent_ts_s.popleft()
            if len(self._group_state.sent_ts_s) < self._limits.messages_per_min_per_group:
                return
            oldest = self._group_state.sent_ts_s[0]
            wait_s = (oldest + window_s) - now + epsilon_s
            if wait_s > 0:
                await self._sleep_fn(wait_s)
            # loop again to re-check after sleeping (another send may
            # have raced in, or the window may have moved further)
