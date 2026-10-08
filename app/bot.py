"""Live signal-only orchestration. All exchange access is public/read-only."""
from __future__ import annotations

import asyncio
import os
from collections import deque
from pathlib import Path

from app.backtest.engine import generate_candidates_at_snapshot, grade_candidates
from app.config import AppConfig
from app.core.errors import SnapshotIncompleteError
from app.core.logging import get_logger
from app.core.models import Direction, NewsState, TakerFlowState, TimestampedValue
from app.core.time_utils import now_ms, utc_date_str
from app.data.binance.models import (RawAggTrade, RawBookTicker, RawDepthSnapshot, RawKline,
    RawLongShortRatio, RawOpenInterest)
from app.data.binance.rest import BinanceRestClient, RestClientConfig
from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig
from app.data.derivatives import POLL_INTERVAL_LIVE_OI_S, build_derivatives_state, merge_live_oi_into_5m_series
from app.data.normalization import merge_kline_series, normalize_kline_series, open_interest_to_timestamped
from app.data.orderbook import depth_and_ticker_to_orderbook_state
from app.data.snapshot import SnapshotInputs, build_snapshot
from app.news.collectors import NewsCollector, build_retry_config, build_source_configs
from app.news.engine import NewsEngine, run_collection_cycle
from app.risk.risk_engine import (DailyCounters, OpenSignalRecord, RiskState, apply_min_rr_gate,
    check_risk_limits, compute_position_size)
from app.risk.veto_engine import run_veto_engine
from app.signals.signal_engine import build_final_signal
from app.telegram.formatter import DeliveryContext
from app.telegram.queue import TelegramQueue, build_telegram_limits
from app.telegram.sender import SendOutcome, TelegramCredentials, TelegramSender
from app.database.repository import SignalRepository

logger = get_logger(__name__)
_STOP = object()
_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


def _depth_from_rest(symbol: str, payload: dict, received: int) -> RawDepthSnapshot:
    return RawDepthSnapshot.from_ws_payload({"b":payload.get("bids",[]),"a":payload.get("asks",[]),
        "E":received,"u":payload.get("lastUpdateId",0)},symbol)

def _ticker_from_rest(symbol: str, payload: dict, received: int) -> RawBookTicker:
    return RawBookTicker.from_ws_payload({"s":payload.get("symbol",symbol),"b":payload["bidPrice"],
        "B":payload["bidQty"],"a":payload["askPrice"],"A":payload["askQty"],"E":received})

def _flow_from_trades(symbol: str, trades: deque, bars, now: int) -> TakerFlowState:
    windows=[]
    for end in [((now//300_000)-2)*300_000,((now//300_000)-1)*300_000,(now//300_000)*300_000]:
        lo=end-300_000; rows=[t for t in trades if lo < t.trade_time_ms <= end]
        windows.append((sum(t.quantity for t in rows if t.is_taker_buy),sum(t.quantity for t in rows)))
    return TakerFlowState(symbol,[x[0] for x in windows],[x[1] for x in windows],now,now)


class LiveSnapshotCache:
    """In-memory adapter joining the existing REST, WS, normalization and snapshot layers."""
    def __init__(self,cfg:AppConfig,rest:BinanceRestClient,ws_factory=BinanceWebSocketClient):
        self.cfg,self.rest,self.ws_factory=cfg,rest,ws_factory
        self.data={}; self.ws=None; self.ws_task=None

    def _streams(self,symbols):
        return [f"{s.lower()}@{stream}" for s in symbols for stream in
                ("aggTrade","depth20@100ms","bookTicker","markPrice",*(f"kline_{tf}" for tf in _TIMEFRAMES))]

    async def start(self,symbols):
        self.ws=self.ws_factory(WebSocketClientConfig(self.cfg.system["binance_ws_base_url"]),self._streams(symbols))
        self.ws_task=asyncio.create_task(self._consume(),name="binance-public-websocket")

    async def backfill(self,symbols):
        for symbol in symbols: self.data[symbol]={"klines":{},"depth":None,"ticker":None,"trades":deque(maxlen=1500),"derivatives":None,"oi":None,"error":None}
        results=await asyncio.gather(*(self._backfill_one(s) for s in symbols),return_exceptions=True)
        for symbol,result in zip(symbols,results):
            if isinstance(result,BaseException):
                self.data[symbol]["error"]=f"backfill: {type(result).__name__}: {result}"
                logger.error("symbol backfill failed",extra={"context":{"symbol":symbol,"error":str(result)}})

    async def _backfill_one(self,symbol):
        c=self.data[symbol]
        calls=[self.rest.klines(symbol,tf,limit=max(100,int(self.cfg.strategy["common"]["min_candles"]))) for tf in _TIMEFRAMES]
        calls += [self.rest.depth(symbol,limit=20),self.rest.book_ticker(symbol),self.rest.agg_trades(symbol,limit=500),
            self.rest.funding_rate(symbol,limit=100),*(self.rest.open_interest_hist(symbol,p,limit=30) for p in ("5m","15m","1h","1d")),
            self.rest.global_long_short_account_ratio(symbol,"5m",30),self.rest.taker_long_short_ratio(symbol,"5m",30),self.rest.open_interest(symbol)]
        results=await asyncio.gather(*calls,return_exceptions=True); received=now_ms()
        required=[*range(7),len(results)-1]
        bad=[(i,results[i]) for i in required if isinstance(results[i],BaseException)]
        if bad: c["error"]="; ".join(f"request[{i}]: {type(e).__name__}: {e}" for i,e in bad); return
        for i,tf in enumerate(_TIMEFRAMES): c["klines"][tf]=normalize_kline_series(symbol,tf,results[i])
        c["depth"]=_depth_from_rest(symbol,results[5],received); c["ticker"]=_ticker_from_rest(symbol,results[6],received)
        if not isinstance(results[7],BaseException):
            c["trades"].extend(RawAggTrade.from_ws_payload({"a":r.get("a",r.get("id")),"p":r["p"],"q":r["q"],"m":r.get("m",r.get("isBuyerMaker")),"T":r.get("T",r.get("time"))}) for r in results[7])
        else: logger.warning("aggTrade backfill unavailable",extra={"context":{"symbol":symbol,"error":str(results[7])}})
        for i in range(8,15):
            if isinstance(results[i],BaseException): logger.warning("optional derivatives backfill unavailable",extra={"context":{"symbol":symbol,"request_index":i,"error":str(results[i])}})
        optional=lambda i: results[i] if not isinstance(results[i],BaseException) else []
        c["derivatives"]=build_derivatives_state(symbol,funding_rows=optional(8),oi_5m_rows=optional(9),oi_15m_rows=optional(10),
            oi_1h_rows=optional(11),oi_1d_rows=optional(12),long_short_account_rows=optional(13),taker_long_short_rows=optional(14),
            premium_index_current=None,received_ts_ms=received)
        c["oi"]=open_interest_to_timestamped(results[15],received)

    async def refresh_live_oi(self):
        if self.data:
            results=await asyncio.gather(*(self.rest.open_interest(s) for s in self.data),return_exceptions=True); received=now_ms()
            for symbol,result in zip(self.data,results):
                if isinstance(result,BaseException): logger.warning("live OI refresh failed",extra={"context":{"symbol":symbol,"error":str(result)}}); continue
                c=self.data[symbol]; c["oi"]=open_interest_to_timestamped(result,received)
                if c["derivatives"]:
                    d=c["derivatives"]; series=merge_live_oi_into_5m_series(d.open_interest_history_5m,c["oi"])
                    c["derivatives"]=type(d)(d.symbol,d.funding_rate_history,series,d.open_interest_history_15m,d.open_interest_history_1h,
                        d.open_interest_history_1d,d.long_short_account_ratio_history,d.taker_long_short_ratio_history,d.premium_index_current)

    async def _consume(self):
        self.ws.set_resync_callback(lambda: asyncio.create_task(self.backfill(list(self.data))))
        async for stream,payload in self.ws.messages():
            try: self._apply_message(stream,payload)
            except Exception as exc: logger.warning("invalid market-data message ignored",extra={"context":{"stream":stream,"error":str(exc)}})

    def _apply_message(self,stream,payload):
        symbol=stream.split("@",1)[0].upper(); c=self.data.get(symbol)
        if c is None: return
        received=now_ms()
        if "@kline_" in stream:
            tf=stream.split("@kline_",1)[1]; old=c["klines"].get(tf)
            raw=RawKline.from_ws_payload(payload)
            if old is not None: c["klines"][tf]=merge_kline_series(old,[raw])
        elif stream.endswith("@depth20@100ms"): c["depth"]=RawDepthSnapshot.from_ws_payload(payload,symbol)
        elif stream.endswith("@bookTicker"): c["ticker"]=RawBookTicker.from_ws_payload(payload)
        elif stream.endswith("@aggTrade"): c["trades"].append(RawAggTrade.from_ws_payload(payload))
        elif stream.endswith("@markPrice") and c["derivatives"] is not None:
            d=c["derivatives"]; value=TimestampedValue(float(payload["p"]),int(payload.get("E",received)),received)
            c["derivatives"]=type(d)(d.symbol,d.funding_rate_history,d.open_interest_history_5m,d.open_interest_history_15m,
                d.open_interest_history_1h,d.open_interest_history_1d,d.long_short_account_ratio_history,
                d.taker_long_short_ratio_history,value)
        c.setdefault("received",{})[stream]=received

    def is_snapshot_ready(self,symbol):
        c=self.data.get(symbol)
        if not c or c["error"] or not c["oi"] or not c["depth"] or not c["ticker"] or c["derivatives"] is None: return False
        if len(c["klines"].get("5m",[]).bars if c["klines"].get("5m") else []) < self.cfg.strategy["common"]["min_candles"]: return False
        if not c["depth"].bids or not c["depth"].asks or c["ticker"].best_bid<=0 or c["ticker"].best_ask<=0: return False
        if sum(x.price*x.quantity for x in c["depth"].bids[:5])<=0 or sum(x.price*x.quantity for x in c["depth"].asks[:5])<=0: return False
        return not (now_ms()-c["oi"].received_ts_ms>self.cfg.system["staleness_budget_ms"]["oi_ms"] or
                    c["oi"].received_ts_ms-c["oi"].event_ts_ms>self.cfg.system["staleness_budget_ms"]["oi_ms"])

    def not_ready(self,symbols): return [s for s in symbols if not self.is_snapshot_ready(s)]

    def get_snapshot(self,symbol):
        if not self.is_snapshot_ready(symbol): raise SnapshotIncompleteError(f"{symbol}: first full snapshot is not ready")
        c=self.data[symbol]; pair=self.cfg.pair_config(symbol); stamp=now_ms()
        ob=depth_and_ticker_to_orderbook_state(c["depth"],c["ticker"],depth_check_levels=5,received_ts_ms=stamp)
        flows=_flow_from_trades(symbol,c["trades"],c["klines"]["5m"].bars,stamp) if c["trades"] else None
        feeds={stream:self.ws.feed_health(stream,stamp) for stream in self._streams([symbol])}
        inputs=SnapshotInputs(symbol,stamp,c["klines"],ob,flows,c["derivatives"],feeds,pair["price_tick"],pair["qty_step"],pair["min_qty"],pair["fee_maker_bps"],pair["fee_taker_bps"],c["oi"],self.cfg.system["staleness_budget_ms"]["oi_ms"])
        return build_snapshot(inputs,min_candles=self.cfg.strategy["common"]["min_candles"])


class SnapshotReadinessError(RuntimeError):
    def __init__(self,symbols):
        self.symbols=list(symbols); super().__init__(f"first snapshot readiness timed out; not ready: {self.symbols}")


class SignalBot:
    def __init__(self,cfg:AppConfig,equity_usd:float,*,rest_client=None,ws_factory=BinanceWebSocketClient,
                 cache_factory=LiveSnapshotCache,repository=None,sender=None):
        self.cfg,self.equity_usd=cfg,equity_usd
        self.rest=rest_client or BinanceRestClient(RestClientConfig(cfg.system["binance_base_url"]))
        self.ws_factory,self.cache_factory=ws_factory,cache_factory
        self.repo=repository or SignalRepository(Path(cfg.config_dir).parent/cfg.system["database_paths"]["sqlite_path"])
        self.sender=sender; self.telegram_queue=None; self.cache=None; self.symbols=[]
        self.news_engine=None; self.news_collector=None; self.news_sources=[]; self.news_task=None
        self.stop_event=None; self.outbox=asyncio.Queue(); self.outbox_task=None; self.oi_task=None
        self.enqueued_count=0; self._closed=False; self._close_lock=asyncio.Lock()

    async def exchange_info(self):
        """Read public exchangeInfo and preserve per-symbol filter parse failures for boot reporting."""
        raw_get=getattr(self.rest,"_get",None)
        if raw_get is None: return await self.rest.exchange_info()
        payload=await raw_get("/fapi/v1/exchangeInfo")
        entries=[]
        for row in payload.get("symbols",[]):
            try: entries.append(RawExchangeInfoSymbol.from_rest_payload(row))
            except Exception as exc:
                entries.append({"symbol":str(row.get("symbol","<unknown>")),"invalid_reason":f"malformed exchange filters: {type(exc).__name__}: {exc}"})
        return entries

    async def start_market_data(self,symbols):
        self.symbols=list(symbols); self.cache=self.cache_factory(self.cfg,self.rest,self.ws_factory)
        await self.cache.start(self.symbols)

    async def wait_for_snapshot_readiness(self,symbols,timeout_s=90.0,min_ready_count=None):
        deadline=asyncio.get_running_loop().time()+timeout_s
        minimum=min(len(symbols),int(self.cfg.system["boot_checks"]["min_symbols_validated"])) if min_ready_count is None else min(len(symbols),int(min_ready_count))
        try: await asyncio.wait_for(self.cache.backfill(symbols),timeout=max(0.001,deadline-asyncio.get_running_loop().time()))
        except asyncio.TimeoutError as exc: raise SnapshotReadinessError(self.cache.not_ready(symbols)) from exc
        while True:
            missing=self.cache.not_ready(symbols)
            if len(symbols)-len(missing)>=minimum and self.cache.ws.is_connected: return
            if self.cache.ws_task.done():
                error=self.cache.ws_task.exception()
                raise RuntimeError(f"WebSocket message task stopped before readiness: {error}")
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0: raise SnapshotReadinessError(missing)
            await asyncio.sleep(min(0.1,remaining))

    async def start_news_collectors(self):
        await self.repo.connect()
        dry_raw=os.getenv("TELEGRAM_DRY_RUN","true").strip().lower()
        if dry_raw not in {"true","false","1","0","yes","no"}: raise ValueError("TELEGRAM_DRY_RUN must be true/false")
        dry_run=dry_raw in {"true","1","yes"}; token=os.getenv("TELEGRAM_BOT_TOKEN",""); chat=os.getenv("TELEGRAM_CHAT_ID","")
        if not dry_run and (not token or not chat): raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when TELEGRAM_DRY_RUN=false")
        self.telegram_queue=TelegramQueue(build_telegram_limits(self.cfg.system))
        self.sender=self.sender or TelegramSender(TelegramCredentials(token,chat or "dry-run",dry_run),self.telegram_queue)
        self.news_engine=NewsEngine(self.cfg.news_sources); self.news_collector=NewsCollector(build_retry_config(self.cfg.news_sources))
        self.news_sources=build_source_configs(self.cfg.news_sources); self.stop_event=self.stop_event or asyncio.Event()
        self.news_task=asyncio.create_task(self._news_loop(),name="news-collectors")

    async def _news_loop(self):
        while not self.stop_event.is_set():
            for source in self.news_sources:
                try: await run_collection_cycle(self.news_collector,self.news_engine,[source],receipt_ts_ms=now_ms())
                except Exception as exc:
                    self.news_engine.record_source_health(source.name,False)
                    logger.warning("news source cycle degraded",extra={"context":{"source":source.name,"error":str(exc)}})
            try: await asyncio.wait_for(self.stop_event.wait(),timeout=60.0)
            except asyncio.TimeoutError: pass

    async def _publisher_loop(self):
        while True:
            item=await self.outbox.get()
            try:
                if item is _STOP: return
                signal,ctx=item
                result=await self.sender.send_signal_message(signal,ctx)
                if result.outcome==SendOutcome.SENT:
                    await self.repo.update_lifecycle_state(signal.signal_id,"PENDING","PUBLISHED",now_ms(),"queued to Telegram (dry-run or delivery accepted)")
                else: logger.warning("signal remains PENDING after Telegram refusal/failure",extra={"context":{"signal_id":signal.signal_id,"outcome":result.outcome.value,"detail":result.detail}})
            except Exception:
                logger.exception("telegram outbox item failed")
            finally: self.outbox.task_done()

    async def _refresh_oi_loop(self):
        while not self.stop_event.is_set():
            try: await self.cache.refresh_live_oi()
            except Exception: logger.exception("live open-interest refresh failed")
            try: await asyncio.wait_for(self.stop_event.wait(),timeout=POLL_INTERVAL_LIVE_OI_S)
            except asyncio.TimeoutError: pass

    async def _risk_state(self,snapshot):
        active=await self.repo.get_open_signals()
        active=[row for row in active if row.expiry_ts_ms>snapshot.as_of_ts_ms]
        total=await self.repo.count_active_signals(now_ts_ms=snapshot.as_of_ts_ms)
        if total!=len(active): raise RuntimeError("active signal query disagrees with lifecycle rows")
        for cluster in self.cfg.risk.get("correlated_clusters",[]):
            n=await self.repo.count_active_by_cluster(cluster["symbols"],now_ts_ms=snapshot.as_of_ts_ms)
            expected=sum(row.symbol in cluster["symbols"] for row in active)
            if n!=expected: raise RuntimeError(f"active cluster query mismatch: {cluster['name']}")
        open_records=[OpenSignalRecord(row.signal_id,row.symbol,Direction(row.direction),row.created_ts_ms) for row in active]
        daily=DailyCounters(utc_date_str(snapshot.as_of_ts_ms),await self.repo.count_signals_today(now_ts_ms=snapshot.as_of_ts_ms),
            await self.repo.daily_realized_loss_r(now_ts_ms=snapshot.as_of_ts_ms))
        cooldown={}; last={}; seconds={}
        for symbol in self.symbols:
            elapsed=await self.repo.seconds_since_last_signal(symbol,now_ts_ms=snapshot.as_of_ts_ms)
            seconds[symbol]=elapsed; last[symbol]=await self.repo.last_signal_direction(symbol)
            if elapsed is not None and elapsed<self.cfg.risk["cooldown_min"]*60:
                cooldown[symbol]=snapshot.as_of_ts_ms+int(self.cfg.risk["cooldown_min"]*60_000-elapsed*1000)
        return RiskState(open_records,daily,cooldown),last,seconds

    async def evaluate_symbol(self,snapshot,news_state):
        candidates=generate_candidates_at_snapshot(snapshot,news_state,self.cfg.strategy)
        for c in candidates:
            await self.repo.insert_candidate_audit(symbol=c.symbol,strategy_source=c.strategy_source,direction=c.direction.value,
                confidence=c.confidence,event_ts_ms=c.event_ts_ms,snapshot_version=snapshot.snapshot_version,meta=c.meta)
        for (symbol,_direction),(grade,confidence,group) in grade_candidates(candidates,self.cfg.strategy["consensus"]).items():
            if grade is None: continue
            candidate=group[0]
            veto=run_veto_engine(snapshot=snapshot,news_state=news_state,candidate=candidate,veto_cfg=self.cfg.veto,
                symbol_tier=self.cfg.symbol_tier(symbol),funding_z=None,btc_trend_direction=None)
            if veto.veto_state.value!="PASS": continue
            if veto.max_grade_cap:
                order={"B":0,"A":1,"A+":2}; grade=min((grade,veto.max_grade_cap),key=lambda g:order.get(g,99))
            state,last_direction,elapsed=await self._risk_state(snapshot)
            violations=check_risk_limits(candidate=candidate,risk_state=state,risk_cfg=self.cfg.risk,
                as_of_ts_ms=snapshot.as_of_ts_ms,current_date_str=utc_date_str(snapshot.as_of_ts_ms))
            previous=last_direction.get(symbol); since=elapsed.get(symbol)
            if previous and previous!=candidate.direction.value and since is not None and since<self.cfg.risk["cooldown_min"]*60:
                violations.append(f"opposite-direction cooldown: last={previous}, candidate={candidate.direction.value}")
            if violations:
                logger.info("candidate blocked by risk limits",extra={"context":{"symbol":symbol,"reasons":violations}}); continue
            pair=self.cfg.pair_config(symbol); entry=(candidate.entry_low+candidate.entry_high)/2
            sizing=compute_position_size(assumed_equity_usd=self.equity_usd,risk_per_trade_pct=self.cfg.risk["risk_per_trade_pct"],
                entry_price=entry,stop_loss=candidate.stop_loss,qty_step=pair["qty_step"],fee_maker_bps=pair["fee_maker_bps"],
                fee_taker_bps=pair["fee_taker_bps"],depth_usd=min(snapshot.orderbook.bid_depth_5lvl_usd,snapshot.orderbook.ask_depth_5lvl_usd))
            if sizing.qty<pair["min_qty"]: continue
            signal=build_final_signal(candidate=candidate,snapshot=snapshot,grade=grade,confidence_weighted=confidence,
                veto_outcome=veto,size_units_advisory=sizing.qty,notional_usd_advisory=sizing.notional_usd,
                expiry_per_grade=self.cfg.risk["expiry_per_grade"],created_ts_ms=snapshot.as_of_ts_ms)
            if signal.veto_state!="PASS" or not apply_min_rr_gate(signal.rr_tp2,self.cfg.risk["min_rr_tp2"]): continue
            await self.repo.insert_signal(signal)
            news_label="clear" if not news_state.active_for(symbol) else "events available"
            self.outbox.put_nowait((signal,DeliveryContext(news_label,"connected",snapshot.as_of_ts_ms)))
            self.enqueued_count+=1
            await self.outbox.join()  # serialize risk reservations before evaluating the next candidate

    async def run(self,stop_event=None):
        self.stop_event=stop_event or self.stop_event or asyncio.Event()
        self.outbox_task=asyncio.create_task(self._publisher_loop(),name="telegram-outbox")
        self.oi_task=asyncio.create_task(self._refresh_oi_loop(),name="live-oi-poller")
        interval=float(os.getenv("SIGNAL_EVALUATION_INTERVAL_S","300"))
        while not self.stop_event.is_set():
            if self.cache.ws_task.done(): raise RuntimeError("market WebSocket consumer stopped")
            stamp=now_ms(); news=self.news_engine.build_news_state(stamp)
            for symbol in self.symbols:
                if self.stop_event.is_set(): break
                try: await self.evaluate_symbol(self.cache.get_snapshot(symbol),news)
                except Exception as exc: logger.exception("symbol evaluation failed; no signal published",extra={"context":{"symbol":symbol,"error":str(exc)}})
            try: await asyncio.wait_for(self.stop_event.wait(),timeout=interval)
            except asyncio.TimeoutError: pass

    async def shutdown(self):
        async with self._close_lock:
            if self._closed: return
            self._closed=True
            if self.stop_event: self.stop_event.set()
            for task in (self.news_task,self.oi_task,self.cache.ws_task if self.cache else None):
                if task and not task.done(): task.cancel()
            await asyncio.gather(*(t for t in (self.news_task,self.oi_task,self.cache.ws_task if self.cache else None) if t),return_exceptions=True)
            if self.outbox_task and not self.outbox_task.done():
                await self.outbox.join(); self.outbox.put_nowait(_STOP); await self.outbox.join(); await self.outbox_task
            for obj in (self.news_collector,self.sender,self.repo,self.rest):
                if obj is not None:
                    try: await obj.close()
                    except Exception: logger.exception("component shutdown failed",extra={"context":{"component":type(obj).__name__}})
