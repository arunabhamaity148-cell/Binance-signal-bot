from __future__ import annotations

import pytest

from app.core.errors import DatabaseWriteError
from app.database.repository import SignalRepository
from app.signals.lifecycle import SignalLifecycleState
from app.signals.models import Signal


def _sample_signal(signal_id="CSB-20260101-AAAAAA") -> Signal:
    return Signal(
        signal_id=signal_id, created_ts_ms=1000, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=0.8, strategy_source="S1",
        entry_low=100.0, entry_high=100.5, stop_loss=99.0,
        tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0, rr_tp2=1.5,
        expiry_ts_ms=2000, why_lines=["r1"], veto_state="PASS", veto_reason=None,
        size_units_advisory=1.0, notional_usd_advisory=100.0, meta={"atr14": 1.0},
    )


@pytest.mark.asyncio
async def test_connect_creates_db_and_schema(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        assert (tmp_path / "test.db").exists()
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_insert_and_get_signal(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        signal = _sample_signal()
        await repo.insert_signal(signal)
        fetched = await repo.get_signal_by_id(signal.signal_id)
        assert fetched is not None
        assert fetched.signal_id == signal.signal_id
        assert fetched.symbol == "BTCUSDT"
        assert fetched.lifecycle_state == SignalLifecycleState.PENDING.value
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_get_signal_by_id_returns_none_when_missing(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        result = await repo.get_signal_by_id("does-not-exist")
        assert result is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_update_lifecycle_state(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        signal = _sample_signal()
        await repo.insert_signal(signal)
        await repo.update_lifecycle_state(
            signal.signal_id, "PENDING", "PUBLISHED", ts_ms=2000, reason="sent to telegram"
        )
        fetched = await repo.get_signal_by_id(signal.signal_id)
        assert fetched.lifecycle_state == "PUBLISHED"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_get_open_signals_filters_by_published_state(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        sig1 = _sample_signal("CSB-20260101-111111")
        sig2 = _sample_signal("CSB-20260101-222222")
        await repo.insert_signal(sig1)
        await repo.insert_signal(sig2)
        await repo.update_lifecycle_state(sig1.signal_id, "PENDING", "PUBLISHED", ts_ms=2000)

        open_signals = await repo.get_open_signals()
        assert len(open_signals) == 1
        assert open_signals[0].signal_id == sig1.signal_id
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_insert_candidate_audit(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        await repo.insert_candidate_audit(
            symbol="BTCUSDT", strategy_source="S1", direction="LONG", confidence=0.7,
            event_ts_ms=1000, snapshot_version="v1", meta={"atr14": 1.5},
        )
        # No direct getter specified in repository for candidate_audit
        # rows (they're for offline analysis); verify indirectly via
        # raw connection query.
        async with repo._conn.execute("SELECT COUNT(*) FROM candidate_audit") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_count_signals_on_date(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        sig1 = _sample_signal("CSB-20260101-333333")
        await repo.insert_signal(sig1)
        count = await repo.count_signals_on_date(0, 5000)
        assert count == 1
        count_outside = await repo.count_signals_on_date(5000, 10000)
        assert count_outside == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_operations_without_connect_raise():
    repo = SignalRepository("/tmp/never-connected.db")
    with pytest.raises(DatabaseWriteError):
        await repo.insert_signal(_sample_signal())


@pytest.mark.asyncio
async def test_duplicate_signal_id_raises_database_write_error(tmp_path):
    repo = SignalRepository(tmp_path / "test.db")
    await repo.connect()
    try:
        signal = _sample_signal()
        await repo.insert_signal(signal)
        with pytest.raises(DatabaseWriteError):
            await repo.insert_signal(signal)  # duplicate primary key
    finally:
        await repo.close()
