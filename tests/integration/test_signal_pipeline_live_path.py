from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from app.bot import SignalBot
from app.config import load_all
from app.core.math import wilder_atr
from app.core.models import NewsState, VetoState
from app.core.time_utils import now_ms
from app.database.repository import SignalRepository
from app.signals.models import Signal
from app.telegram.sender import SendOutcome, SendResult
from tests.integration.test_pipeline_e2e import _news, build_realistic_s1_snapshot


class _ClosedRest:
    async def close(self): pass


class _RecordingSender:
    def __init__(self): self.sent=[]; self.closed=False
    async def send_signal_message(self,signal,context):
        assert signal.veto_state=="PASS"
        self.sent.append((signal,context))
        return SendResult(SendOutcome.SENT)
    async def close(self): self.closed=True


def _tradable_snapshot():
    """Strengthen only synthetic bar data; all configured thresholds stay unchanged."""
    snapshot=build_realistic_s1_snapshot(); bars=list(snapshot.klines["5m"].bars)
    bars[-2]=replace(bars[-2],low=99_000.0)
    atr=wilder_atr(bars,14)
    l_high=max(bar.high for bar in bars[-4:-1])
    bars[-1]=replace(bars[-1],high=l_high+0.7*atr)
    klines=dict(snapshot.klines); klines["5m"]=replace(klines["5m"],bars=bars)
    return replace(snapshot,klines=klines)


def _signal(signal_id,created,expiry,symbol="BTCUSDT",direction="LONG"):
    prices=(dict(stop_loss=99.0,tp1=101.0,tp2=102.0,tp3=103.0,tp4=104.0) if direction=="LONG"
            else dict(stop_loss=101.5,tp1=99.5,tp2=99.0,tp3=98.5,tp4=98.0))
    return Signal(signal_id=signal_id,created_ts_ms=created,symbol=symbol,direction=direction,grade="B",confidence=0.7,
        strategy_source="S1",entry_low=100.0,entry_high=100.5,**prices,
        rr_tp2=2.0,expiry_ts_ms=expiry,why_lines=["manual outcome ledger test"],veto_state="PASS",veto_reason=None,
        size_units_advisory=1.0,notional_usd_advisory=100.0,meta={})


@pytest.mark.asyncio
async def test_synthetic_market_snapshot_reaches_sqlite_and_telegram_outbox(tmp_path):
    cfg=load_all(); repo=SignalRepository(tmp_path/"live-path.db"); await repo.connect()
    sender=_RecordingSender(); bot=SignalBot(cfg,1000.0,rest_client=_ClosedRest(),repository=repo,sender=sender)
    bot.symbols=["BTCUSDT"]; bot.outbox_task=__import__("asyncio").create_task(bot._publisher_loop())
    snapshot=_tradable_snapshot(); news=_news(snapshot)
    try:
        await bot.evaluate_symbol(snapshot,news)
        assert bot.enqueued_count==1
        assert len(sender.sent)==1
        rows=await repo.get_open_signals()
        assert len(rows)==1 and rows[0].symbol=="BTCUSDT" and rows[0].lifecycle_state=="PUBLISHED"
        assert rows[0].veto_state=="PASS"
        assert await repo.count_signals_today(now_ts_ms=snapshot.as_of_ts_ms)==1
        # The derived per-symbol cooldown blocks an immediate repeat without tuning its configured duration.
        await bot.evaluate_symbol(snapshot,news)
        assert bot.enqueued_count==1 and len(sender.sent)==1
    finally:
        await bot.shutdown()
    assert sender.closed


@pytest.mark.asyncio
async def test_veto_blocked_candidate_is_never_persisted_or_enqueued(tmp_path,monkeypatch,caplog):
    import app.bot as bot_module
    cfg=load_all(); repo=SignalRepository(tmp_path/"blocked.db"); await repo.connect()
    sender=_RecordingSender(); bot=SignalBot(cfg,1000.0,rest_client=_ClosedRest(),repository=repo,sender=sender)
    bot.symbols=["BTCUSDT"]; bot.outbox_task=__import__("asyncio").create_task(bot._publisher_loop())
    snapshot=_tradable_snapshot(); news=_news(snapshot)
    def blocked(**kwargs):
        class Result:
            veto_state=VetoState.BLOCK
            veto_reason="test hard block"
            max_grade_cap=None
            guard_results=[]
        return Result()
    monkeypatch.setattr(bot_module,"run_veto_engine",blocked)
    try:
        with caplog.at_level(logging.INFO,logger="app.bot"):
            await bot.evaluate_symbol(snapshot,news)
        async with repo._conn.execute("SELECT COUNT(*) FROM signals") as cur: signals=(await cur.fetchone())[0]
        async with repo._conn.execute("SELECT COUNT(*) FROM candidate_audit") as cur: audits=(await cur.fetchone())[0]
        assert signals==0
        assert audits>=1
        assert bot.enqueued_count==0 and sender.sent==[]
        assert any(record.getMessage()=="strategy_eval" for record in caplog.records)
        assert any(record.getMessage()=="candidate" and record.context.get("stage")=="rejected"
                   and record.context.get("veto")=="BLOCK" and record.context.get("reason")=="test hard block"
                   for record in caplog.records)
    finally: await bot.shutdown()


@pytest.mark.asyncio
async def test_derived_risk_queries_and_manual_outcome_ledger(tmp_path):
    repo=SignalRepository(tmp_path/"risk-ledger.db"); await repo.connect()
    try:
        now=now_ms(); sig=_signal("CSB-20261008-A1B2C3",now,now+3_600_000)
        await repo.insert_signal(sig)
        assert await repo.count_active_signals(now_ts_ms=now)==0
        await repo.update_lifecycle_state(sig.signal_id,"PENDING","PUBLISHED",now,"dry-run publication")
        assert await repo.count_active_signals(now_ts_ms=now)==1
        assert await repo.count_active_by_cluster(["BTCUSDT","ETHUSDT"],now_ts_ms=now)==1
        assert await repo.count_signals_today(now_ts_ms=now)==1
        assert await repo.seconds_since_last_signal("BTCUSDT",now_ts_ms=now+30_000)==30.0
        assert await repo.last_signal_direction("BTCUSDT")=="LONG"
        assert await repo.daily_realized_loss_r(now_ts_ms=now)==0.0
        pending=_signal("CSB-20261008-C3D4E5",now+1,now+3_600_000,"ETHUSDT","SHORT")
        await repo.insert_signal(pending)
        assert await repo.count_signals_today(now_ts_ms=now+1)==2
        await repo.record_outcome(sig.signal_id,1.25,"operator recorded",recorded_ts_ms=now)
        assert await repo.daily_realized_loss_r(now_ts_ms=now)==1.25
        with pytest.raises(ValueError,match="provenance"):
            await repo.record_outcome(sig.signal_id,1.0,"rejected",provenance="automated",recorded_ts_ms=now)
        async with repo._conn.execute("SELECT provenance,note FROM outcomes WHERE signal_id=?",(sig.signal_id,)) as cur:
            row=await cur.fetchone()
        assert row==("manual","operator recorded")
        async with repo._conn.execute("SELECT MAX(version) FROM schema_migrations") as cur:
            version=(await cur.fetchone())[0]
        assert version==3
    finally: await repo.close()


@pytest.mark.asyncio
async def test_daily_loss_risk_gate_uses_operator_ledger_and_keeps_threshold(tmp_path):
    cfg=load_all(); repo=SignalRepository(tmp_path/"loss-gate.db"); await repo.connect()
    sender=_RecordingSender(); bot=SignalBot(cfg,1000.0,rest_client=_ClosedRest(),repository=repo,sender=sender)
    bot.symbols=["BTCUSDT"]; bot.outbox_task=__import__("asyncio").create_task(bot._publisher_loop())
    snapshot=_tradable_snapshot(); news=_news(snapshot)
    existing=_signal("CSB-20261008-D4E5F6",snapshot.as_of_ts_ms-60_000,snapshot.as_of_ts_ms+3_600_000,"ETHUSDT")
    try:
        await repo.insert_signal(existing)
        await repo.update_lifecycle_state(existing.signal_id,"PENDING","PUBLISHED",snapshot.as_of_ts_ms,"prior paper signal")
        await repo.record_outcome(existing.signal_id,cfg.risk["max_daily_loss_r"],"operator loss record",recorded_ts_ms=snapshot.as_of_ts_ms)
        await bot.evaluate_symbol(snapshot,news)
        assert await repo.daily_realized_loss_r(now_ts_ms=snapshot.as_of_ts_ms)==cfg.risk["max_daily_loss_r"]
        assert bot.enqueued_count==0 and sender.sent==[]
    finally: await bot.shutdown()
