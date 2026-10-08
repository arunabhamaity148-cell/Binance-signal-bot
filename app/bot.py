"""Live signal-only orchestration. All exchange access is public/read-only."""
from __future__ import annotations

import asyncio
import os
import time
from collections import Counter, deque
from dataclasses import replace
from pathlib import Path

from app.backtest.engine import generate_candidates_at_snapshot, grade_candidates
from app.config import AppConfig
from app.core.errors import SnapshotIncompleteError
from app.core.logging import get_logger
from app.core.models import Direction, GuardAction, NewsState, TakerFlowState, TimestampedValue
from app.core.time_utils import now_ms, utc_date_str
from app.data.binance.models import (RawAggTrade, RawBookTicker, RawDepthSnapshot, RawExchangeInfoSymbol,
    RawKline, RawLongShortRatio, RawOpenInterest)
from app.data.binance.rest import BinanceRestClient, RestClientConfig
from app.data.binance.websocket import BinanceWebSocketClient, WebSocketClientConfig
from app.data.derivatives import POLL_INTERVAL_LIVE_OI_S, build_derivatives_state, merge_live_oi_into_5m_series
from app.data.normalization import (funding_rate_to_timestamped, long_short_ratio_to_timestamped,
    merge_kline_series, normalize_kline_series, open_interest_to_timestamped,
    taker_long_short_ratio_to_timestamped)
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
from app.telegram.error_notifier import ErrorNotifier
from app.telegram.reports import run_daily_report_loop

logger = get_logger(__name__)
_STOP = object()
_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
DERIVATIVES_REFRESH_INTERVAL_S = 15 * 60  # class E; operator-approved default
DERIVATIVES_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000


def _merge_timestamped_history(existing, raw_rows, converter, received_ts_ms, start_time_ms, end_time_ms):
    """Merge one endpoint's rows by event timestamp; replays are idempotent.

    Older points are retained because S3/S5 use the 1d OI history for a
    multi-day percentile window; the API request itself is bounded to 24h.
    """
    by_event = {point.event_ts_ms: point for point in existing}
    for raw in raw_rows:
        point = converter(raw, received_ts_ms)
        if not start_time_ms <= point.event_ts_ms <= end_time_ms:
            continue
        old = by_event.get(point.event_ts_ms)
        if old is None or point.received_ts_ms >= old.received_ts_ms:
            by_event[point.event_ts_ms] = point
    return [by_event[stamp] for stamp in sorted(by_event)]


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
        self.ws=self.ws_factory(WebSocketClientConfig(
            self.cfg.system["binance_ws_base_url"], binance_env=self.cfg.system["binance_env"]
        ),self._streams(symbols))
        self.ws_task=asyncio.create_task(self._consume(),name="binance-public-websocket")

    async def backfill(self,symbols):
        # A WebSocket reconnect can request a resync while the first boot
        # backfill is still running. Preserve the live cache rows so a
        # concurrent resync cannot replace them with empty placeholders.
        for symbol in symbols:
            self.data.setdefault(symbol,{"klines":{},"depth":None,"ticker":None,
                "trades":deque(maxlen=1500),"derivatives":None,"oi":None,"error":None})
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
        if bad:
            request_names=("klines:5m","klines:15m","klines:1h","klines:4h","klines:1d","depth","bookTicker",
                "aggTrades","fundingRate","openInterestHist:5m","openInterestHist:15m","openInterestHist:1h",
                "openInterestHist:1d","globalLongShortAccountRatio","takerlongshortRatio","openInterest")
            failures=[{"request":request_names[i],"error":f"{type(exc).__name__}: {exc}"} for i,exc in bad]
            c["error"]="; ".join(f"{item['request']}: {item['error']}" for item in failures)
            logger.error("required symbol backfill failed",extra={"context":{"symbol":symbol,"failures":failures}})
            return
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
        c["error"]=None

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

    async def refresh_historical_derivatives(self, *, as_of_ts_ms: int | None = None):
        """Refresh four OI periods, funding, global-account and taker ratios per symbol.

        Each request asks for the prior 24 hours. Results are merged into the
        existing history by endpoint/symbol/event timestamp, preserving older
        percentile history and leaving a failed endpoint's cache unchanged.
        """
        if not self.data:
            return
        as_of = now_ms() if as_of_ts_ms is None else int(as_of_ts_ms)
        start = as_of - DERIVATIVES_HISTORY_WINDOW_MS
        # Binance USDⓈ-M published-weight accounting (official market-data docs,
        # checked 2026-10-08): 20 symbols × /fapi/v1/openInterest weight 1 ×
        # two polls/min (30 s) = 40 weight/min. The 15-minute history cycle is
        # 7 × 20 = 140 requests/cycle = 9.333 requests/min averaged: four
        # /futures/data/openInterestHist calls plus global and taker ratios.
        # Binance lists IP Weight 0 for those six /futures/data calls (with
        # separate 1000-requests/5-min IP limits). For /fapi/v1/fundingRate,
        # Binance documents a shared 500-requests/5-min/IP cap but gives no
        # numeric IP weight. Therefore total recurring weight is 40 +
        # (20/15 × unknown fundingRate weight) per minute; exact total is not
        # computable from published weights. The known subtotal is 40/2400 =
        # 1.67%, not the total. No other scheduled Binance REST call exists:
        # evaluation uses cached/WebSocket data and news polling is external.
        # WebSocket resync repeats a non-periodic backfill burst. Cold boot's
        # one-time known subtotal is 701 weight: exchangeInfo 1, five klines
        # × 20 symbols at limit 100 (weight 2 each), depth20 and bookTicker
        # (weight 2 each), aggTrades limit 500 (weight 20), and openInterest
        # (weight 1); add 20 fundingRate calls of undocumented numeric weight.
        # Sources: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
        for symbol, cache in self.data.items():
            requests = [
                ("fundingRate", "funding_rate_history", self.rest.funding_rate(
                    symbol, limit=500, start_time_ms=start, end_time_ms=as_of), funding_rate_to_timestamped),
                ("openInterestHist:5m", "open_interest_history_5m", self.rest.open_interest_hist(
                    symbol, "5m", limit=500, start_time_ms=start, end_time_ms=as_of), open_interest_to_timestamped),
                ("openInterestHist:15m", "open_interest_history_15m", self.rest.open_interest_hist(
                    symbol, "15m", limit=500, start_time_ms=start, end_time_ms=as_of), open_interest_to_timestamped),
                ("openInterestHist:1h", "open_interest_history_1h", self.rest.open_interest_hist(
                    symbol, "1h", limit=100, start_time_ms=start, end_time_ms=as_of), open_interest_to_timestamped),
                ("openInterestHist:1d", "open_interest_history_1d", self.rest.open_interest_hist(
                    symbol, "1d", limit=30, start_time_ms=start, end_time_ms=as_of), open_interest_to_timestamped),
                ("globalLongShortAccountRatio", "long_short_account_ratio_history", self.rest.global_long_short_account_ratio(
                    symbol, "5m", limit=500, start_time_ms=start, end_time_ms=as_of), long_short_ratio_to_timestamped),
                # NOTE: /futures/data/takerlongshortRatio history is refreshed here
                # for completeness and future cross-check capability, but is NOT
                # currently consumed by any strategy's gating logic. S3 and S5 use
                # the realtime aggTrade-derived taker_buy_ratio instead (different
                # semantics: 5-min aggregated global ratio vs per-message flow).
                # Retained deliberately so that if a future strategy consumes it,
                # the series stays fresh. See docs/KNOWN_UNCERTAINTIES.md.
                ("takerlongshortRatio", "taker_long_short_ratio_history", self.rest.taker_long_short_ratio(
                    symbol, "5m", limit=500, start_time_ms=start, end_time_ms=as_of), taker_long_short_ratio_to_timestamped),
            ]
            results = await asyncio.gather(*(item[2] for item in requests), return_exceptions=True)
            derivatives = cache.get("derivatives")
            if derivatives is None:
                logger.error("derivatives history refresh skipped; no initialized state", extra={"context": {"symbol": symbol}})
                continue
            received = now_ms()
            updates = {}
            for (endpoint, field, _request, converter), result in zip(requests, results):
                if isinstance(result, BaseException):
                    logger.error("derivatives history endpoint refresh failed", extra={
                        "context": {"symbol": symbol, "endpoint": endpoint, "error": f"{type(result).__name__}: {result}"}
                    })
                    continue
                updates[field] = _merge_timestamped_history(
                    getattr(derivatives, field), result, converter, received, start, as_of
                )
            if updates:
                cache["derivatives"] = replace(derivatives, **updates)

    async def _consume(self):
        self.ws.set_resync_callback(self._schedule_reconnect_resync)
        async for stream,payload in self.ws.messages():
            try: self._apply_message(stream,payload)
            except Exception as exc: logger.warning("invalid market-data message ignored",extra={"context":{"stream":stream,"error":str(exc)}})

    def _schedule_reconnect_resync(self):
        symbols=list(self.data)
        return asyncio.create_task(self._rehydrate_after_reconnect(symbols),name="binance-ws-rest-rehydration")

    async def _rehydrate_after_reconnect(self,symbols):
        await self.backfill(symbols)
        ready=[symbol for symbol in symbols if self.is_snapshot_ready(symbol)]
        stale=[symbol for symbol in symbols if symbol not in set(ready)]
        logger.warning("ws_reconnect_complete",extra={"context":{"rehydrated":len(ready),"stale":len(stale)}})
        for symbol in stale:
            ages=self.diagnostic_ages_ms(symbol,now_ms())
            available=[age for age in ages.values() if age is not None]
            logger.warning("ws_reconnect_no_rehydrate",extra={"context":{"symbol":symbol,
                "last_data_age_ms":max(available) if available else None,
                "missing_components":self.snapshot_missing_components(symbol)}})
        return bool(symbols) and not stale

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
        if not c or c.get("error"): return False
        missing=set(self.snapshot_missing_components(symbol))
        # Preserve the existing readiness gate; taker flow remains an optional snapshot input.
        return not missing.intersection({"missing=kline","missing=orderbook","missing=derivatives","missing=oi"})

    @staticmethod
    def _diagnostic_age_ms(as_of_ts_ms,timestamps):
        valid=[int(value) for value in timestamps if value is not None and int(value)>0]
        return None if not valid else max(0,int(as_of_ts_ms)-min(valid))

    def diagnostic_ages_ms(self,symbol,as_of_ts_ms=None):
        """Return observational component ages; these values do not affect readiness."""
        stamp=now_ms() if as_of_ts_ms is None else int(as_of_ts_ms)
        c=self.data.get(symbol,{})
        received=c.get("received",{})
        lower=symbol.lower()
        five=c.get("klines",{}).get("5m")
        bars=getattr(five,"bars",()) if five is not None else ()
        kline_stamp=getattr(bars[-1],"close_time_ms",None) if bars else None
        depth,ticker=c.get("depth"),c.get("ticker")
        depth_stamp=received.get(f"{lower}@depth20@100ms") or getattr(depth,"event_time_ms",None)
        ticker_stamp=received.get(f"{lower}@bookTicker") or getattr(ticker,"event_time_ms",None)
        deriv=c.get("derivatives")
        deriv_stamps=[]
        if deriv is not None:
            for field in ("funding_rate_history","open_interest_history_5m","open_interest_history_15m",
                          "open_interest_history_1h","open_interest_history_1d",
                          "long_short_account_ratio_history","taker_long_short_ratio_history"):
                series=getattr(deriv,field,()) or ()
                if series: deriv_stamps.append(getattr(series[-1],"event_ts_ms",None))
            premium=getattr(deriv,"premium_index_current",None)
            if premium is not None: deriv_stamps.append(getattr(premium,"event_ts_ms",None))
        trades=c.get("trades") or ()
        trade_stamp=received.get(f"{lower}@aggTrade")
        if trade_stamp is None and trades:
            trade_stamp=max((getattr(row,"trade_time_ms",0) for row in trades),default=0)
        oi=c.get("oi")
        return {
            "kline_age_ms":self._diagnostic_age_ms(stamp,[kline_stamp]),
            "book_age_ms":self._diagnostic_age_ms(stamp,[depth_stamp,ticker_stamp]),
            "deriv_age_ms":self._diagnostic_age_ms(stamp,deriv_stamps),
            "taker_age_ms":self._diagnostic_age_ms(stamp,[trade_stamp]),
            "oi_age_ms":self._diagnostic_age_ms(stamp,[getattr(oi,"received_ts_ms",None)]),
        }

    def snapshot_missing_components(self,symbol):
        """Return labeled snapshot components that are currently absent or invalid."""
        c=self.data.get(symbol)
        if not c:
            return ["missing=kline","missing=orderbook","missing=derivatives","missing=taker_flow","missing=oi"]
        missing=[]
        five_min=c.get("klines",{}).get("5m")
        min_candles=int(self.cfg.strategy["common"]["min_candles"])
        if five_min is None or len(five_min.bars)<min_candles:
            missing.append("missing=kline")
        depth,ticker=c.get("depth"),c.get("ticker")
        if (depth is None or ticker is None or not depth.bids or not depth.asks or
                ticker.best_bid<=0 or ticker.best_ask<=0 or
                sum(x.price*x.quantity for x in depth.bids[:5])<=0 or
                sum(x.price*x.quantity for x in depth.asks[:5])<=0):
            missing.append("missing=orderbook")
        if c.get("derivatives") is None:
            missing.append("missing=derivatives")
        if not c.get("trades"):
            missing.append("missing=taker_flow")
        oi=c.get("oi")
        oi_budget=int(self.cfg.system["staleness_budget_ms"]["oi_ms"])
        if (oi is None or now_ms()-oi.received_ts_ms>oi_budget or
                oi.received_ts_ms-oi.event_ts_ms>oi_budget):
            missing.append("missing=oi")
        return missing

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
        self.rest=rest_client or BinanceRestClient(RestClientConfig(
            cfg.system["binance_base_url"], binance_env=cfg.system["binance_env"]
        ))
        self.ws_factory,self.cache_factory=ws_factory,cache_factory
        self.repo=repository or SignalRepository(Path(cfg.config_dir).parent/cfg.system["database_paths"]["sqlite_path"])
        self.sender=sender; self.telegram_queue=None; self.cache=None; self.symbols=[]
        self.news_engine=None; self.news_collector=None; self.news_sources=[]; self.news_task=None
        self.error_notifier=None; self.report_task=None
        self.stop_event=None; self.outbox=asyncio.Queue(); self.outbox_task=None; self.oi_task=None; self.derivatives_task=None
        self.enqueued_count=0; self._closed=False; self._close_lock=asyncio.Lock()
        self._cycle_candidates=0; self._cycle_signals=0
        self._boot_started_monotonic=time.monotonic(); self._snapshot_diagnostics={}

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

    async def start_error_notifier(self):
        """Attach the asynchronous root-log handler before public-feed startup."""
        if self.error_notifier is not None:
            return
        raw=os.getenv("TELEGRAM_DRY_RUN","true").strip().lower()
        if raw not in {"true","false","1","0","yes","no"}:
            raise ValueError("TELEGRAM_DRY_RUN must be true/false")
        dry_run=raw in {"true","1","yes"}
        token=os.getenv("TELEGRAM_BOT_TOKEN",""); error_chat=os.getenv("TELEGRAM_ERROR_CHAT_ID","")
        if not dry_run and (not token or not error_chat):
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_ERROR_CHAT_ID are required when TELEGRAM_DRY_RUN=false")
        await self.repo.connect()
        self.error_notifier=ErrorNotifier(self.repo,bot_token=token,chat_id=error_chat,dry_run=dry_run,
            telegram_limits=build_telegram_limits(self.cfg.system))
        await self.error_notifier.start()

    async def start_market_data(self,symbols):
        self.symbols=list(symbols); self.cache=self.cache_factory(self.cfg,self.rest,self.ws_factory)
        await self.cache.start(self.symbols)

    def _log_snapshot_incomplete(self,symbol,error=None,*,force=False):
        if self.cache is None:
            return
        diagnostic_fn=getattr(self.cache,"snapshot_missing_components",None)
        missing=diagnostic_fn(symbol) if callable(diagnostic_fn) else ["missing=unknown"]
        cache_data=getattr(self.cache,"data",{})
        cache_row=cache_data.get(symbol,{})
        if cache_row.get("error") and "missing=backfill" not in missing:
            missing.append("missing=backfill")
        if not missing:
            missing=["missing=unknown"]
        now=time.monotonic(); signature=tuple(missing)
        previous=self._snapshot_diagnostics.get(symbol)
        if not force and previous and previous[0]==signature and now-previous[1]<10.0:
            return
        self._snapshot_diagnostics[symbol]=(signature,now)
        five_min=cache_row.get("klines",{}).get("5m")
        depth,ticker=cache_row.get("depth"),cache_row.get("ticker")
        oi=cache_row.get("oi")
        component_state={"cache_entry_present":symbol in cache_data,
            "kline_5m_bars":len(five_min.bars) if five_min is not None else 0,
            "min_candles":int(self.cfg.strategy["common"]["min_candles"]),
            "depth_present":depth is not None,"ticker_present":ticker is not None,
            "derivatives_present":cache_row.get("derivatives") is not None,
            "trade_count":len(cache_row.get("trades") or ()),"oi_present":oi is not None,
            "oi_age_ms":now_ms()-oi.received_ts_ms if oi is not None else None,
            "oi_event_lag_ms":oi.received_ts_ms-oi.event_ts_ms if oi is not None else None}
        context={"symbol":symbol,"elapsed_boot_s":round(max(0.0,now-self._boot_started_monotonic),2),
                 "missing_components":missing}
        if cache_row.get("error"):
            context["backfill_error"]=cache_row["error"]
        if error is not None:
            context["error"]=str(error)
        context["component_state"]=component_state
        logger.warning("snapshot incomplete; signal evaluation blocked",extra={"context":context})

    def _snapshot_check_context(self,symbol,as_of_ts_ms):
        cache=self.cache
        missing_fn=getattr(cache,"snapshot_missing_components",None)
        missing=missing_fn(symbol) if callable(missing_fn) else ["missing=unknown"]
        ready_fn=getattr(cache,"is_snapshot_ready",None)
        ready=bool(ready_fn(symbol)) if callable(ready_fn) else not bool(missing)
        names={"missing=kline":"kline","missing=orderbook":"book","missing=derivatives":"deriv",
               "missing=taker_flow":"taker","missing=oi":"oi","missing=backfill":"backfill",
               "missing=unknown":"unknown"}
        cache_row=getattr(cache,"data",{}).get(symbol,{})
        if cache_row.get("error") and "missing=backfill" not in missing:
            missing=list(missing)+["missing=backfill"]
        ages_fn=getattr(cache,"diagnostic_ages_ms",None)
        ages=ages_fn(symbol,as_of_ts_ms) if callable(ages_fn) else {}
        ws=getattr(cache,"ws",None)
        return {"symbol":symbol,"ready":ready,
                "missing":[names.get(item,item.removeprefix("missing=")) for item in missing],
                "missing_raw":missing,
                "kline_age_ms":ages.get("kline_age_ms"),"book_age_ms":ages.get("book_age_ms"),
                "deriv_age_ms":ages.get("deriv_age_ms"),"taker_age_ms":ages.get("taker_age_ms"),
                "oi_age_ms":ages.get("oi_age_ms"),"ws_connected":bool(getattr(ws,"is_connected",False))}

    async def wait_for_snapshot_readiness(self,symbols,timeout_s=90.0,min_ready_count=None):
        deadline=asyncio.get_running_loop().time()+timeout_s
        minimum=min(len(symbols),int(self.cfg.system["boot_checks"]["min_symbols_validated"])) if min_ready_count is None else min(len(symbols),int(min_ready_count))
        try: await asyncio.wait_for(self.cache.backfill(symbols),timeout=max(0.001,deadline-asyncio.get_running_loop().time()))
        except asyncio.TimeoutError as exc: raise SnapshotReadinessError(self.cache.not_ready(symbols)) from exc
        while True:
            missing=self.cache.not_ready(symbols)
            for symbol in missing:
                self._log_snapshot_incomplete(symbol)
            if len(symbols)-len(missing)>=minimum and self.cache.ws.is_connected: return
            if self.cache.ws_task.done():
                error=self.cache.ws_task.exception()
                raise RuntimeError(f"WebSocket message task stopped before readiness: {error}")
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:
                for symbol in missing:
                    self._log_snapshot_incomplete(symbol,force=True)
                raise SnapshotReadinessError(missing)
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
        self.report_task=asyncio.create_task(run_daily_report_loop(
            self.repo,self.sender,self.cfg.risk["paper_trading_assumptions"],self.stop_event
        ),name="daily-report-2359-ist")

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

    async def _refresh_derivatives_history_loop(self, interval_s: float = DERIVATIVES_REFRESH_INTERVAL_S):
        while not self.stop_event.is_set():
            try:
                await self.cache.refresh_historical_derivatives()
            except Exception:
                logger.exception("historical derivatives refresh cycle failed")
            try: await asyncio.wait_for(self.stop_event.wait(), timeout=interval_s)
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

    def _log_candidate(self,candidate,*,grade,veto,reason,stage):
        logger.info("candidate",extra={"context":{"symbol":candidate.symbol,
            "strategy":candidate.strategy_source,"direction":candidate.direction.value,
            "grade":grade,"veto":veto,"reason":reason,"stage":stage}})

    async def evaluate_symbol(self,snapshot,news_state):
        try:
            candidates=generate_candidates_at_snapshot(snapshot,news_state,self.cfg.strategy)
        except Exception as exc:
            logger.info("strategy_eval",extra={"context":{"symbol":getattr(snapshot,"symbol","UNKNOWN"),
                "s1_cand":0,"s2_cand":0,"s3_cand":0,"s4_cand":0,"s5_cand":0,
                "evaluation_error":f"{type(exc).__name__}: {exc}"}})
            raise
        counts=Counter(c.strategy_source for c in candidates)
        log_symbol=getattr(snapshot,"symbol",None) or (candidates[0].symbol if candidates else "UNKNOWN")
        logger.info("strategy_eval",extra={"context":{"symbol":log_symbol,
            **{f"s{i}_cand":counts.get(f"S{i}",0) for i in range(1,6)}}})
        self._cycle_candidates+=len(candidates)
        for c in candidates:
            await self.repo.insert_candidate_audit(symbol=c.symbol,strategy_source=c.strategy_source,direction=c.direction.value,
                confidence=c.confidence,event_ts_ms=c.event_ts_ms,snapshot_version=snapshot.snapshot_version,meta=c.meta)
            self._log_candidate(c,grade="UNASSESSED",veto="NOT_RUN",reason="created",stage="created")
        for (symbol,_direction),(grade,confidence,group) in grade_candidates(candidates,self.cfg.strategy["consensus"]).items():
            grade_label=getattr(grade,"value",grade) if grade is not None else "NONE"
            if grade is None:
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="NOT_RUN",reason="consensus_grade_missing",stage="rejected")
                continue
            candidate=group[0]
            veto=run_veto_engine(snapshot=snapshot,news_state=news_state,candidate=candidate,veto_cfg=self.cfg.veto,
                symbol_tier=self.cfg.symbol_tier(symbol),funding_z=None,btc_trend_direction=None)
            if veto.veto_state.value!="PASS":
                reason=veto.veto_reason or "; ".join(r.reason or r.guard_name for r in veto.guard_results
                    if not r.passed and r.action==GuardAction.BLOCK) or "veto_blocked"
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="BLOCK",reason=reason,stage="rejected")
                for result in veto.guard_results:
                    if not result.passed and result.action == GuardAction.BLOCK:
                        await self.repo.record_veto_block(guard_name=result.guard_name,symbol=symbol,
                            strategy_source=candidate.strategy_source,event_ts_ms=snapshot.as_of_ts_ms,
                            message=result.reason or "blocked")
                continue
            if veto.max_grade_cap:
                order={"B":0,"A":1,"A+":2}; grade=min((grade,veto.max_grade_cap),key=lambda g:order.get(g,99))
                grade_label=getattr(grade,"value",grade)
            state,last_direction,elapsed=await self._risk_state(snapshot)
            violations=check_risk_limits(candidate=candidate,risk_state=state,risk_cfg=self.cfg.risk,
                as_of_ts_ms=snapshot.as_of_ts_ms,current_date_str=utc_date_str(snapshot.as_of_ts_ms))
            previous=last_direction.get(symbol); since=elapsed.get(symbol)
            if previous and previous!=candidate.direction.value and since is not None and since<self.cfg.risk["cooldown_min"]*60:
                violations.append(f"opposite-direction cooldown: last={previous}, candidate={candidate.direction.value}")
            if violations:
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="PASS",reason="risk_limit: "+"; ".join(violations),stage="rejected")
                logger.info("candidate blocked by risk limits",extra={"context":{"symbol":symbol,"reasons":violations}}); continue
            pair=self.cfg.pair_config(symbol); entry=(candidate.entry_low+candidate.entry_high)/2
            sizing=compute_position_size(assumed_equity_usd=self.equity_usd,risk_per_trade_pct=self.cfg.risk["risk_per_trade_pct"],
                entry_price=entry,stop_loss=candidate.stop_loss,qty_step=pair["qty_step"],fee_maker_bps=pair["fee_maker_bps"],
                fee_taker_bps=pair["fee_taker_bps"],depth_usd=min(snapshot.orderbook.bid_depth_5lvl_usd,snapshot.orderbook.ask_depth_5lvl_usd))
            if sizing.qty<pair["min_qty"]:
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="PASS",reason="quantity_below_pair_minimum",stage="rejected")
                continue
            signal=build_final_signal(candidate=candidate,snapshot=snapshot,grade=grade,confidence_weighted=confidence,
                veto_outcome=veto,size_units_advisory=sizing.qty,notional_usd_advisory=sizing.notional_usd,
                expiry_per_grade=self.cfg.risk["expiry_per_grade"],created_ts_ms=snapshot.as_of_ts_ms)
            if signal.veto_state!="PASS":
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="BLOCK",reason=signal.veto_reason or "final_signal_veto",stage="rejected")
                continue
            if not apply_min_rr_gate(signal.rr_tp2,self.cfg.risk["min_rr_tp2"]):
                for item in group:
                    self._log_candidate(item,grade=grade_label,veto="PASS",reason="minimum_rr_gate",stage="rejected")
                continue
            await self.repo.insert_signal(signal)
            self._cycle_signals+=1
            for item in group:
                reason="signal_persisted" if item is candidate else "consensus_contributor_to_persisted_signal"
                self._log_candidate(item,grade=grade_label,veto="PASS",reason=reason,stage="accepted")
            news_label="clear" if not news_state.active_for(symbol) else "events available"
            self.outbox.put_nowait((signal,DeliveryContext(news_label,"connected",snapshot.as_of_ts_ms)))
            self.enqueued_count+=1
            await self.outbox.join()  # serialize risk reservations before evaluating the next candidate

    async def run(self,stop_event=None):
        self.stop_event=stop_event or self.stop_event or asyncio.Event()
        self.outbox_task=asyncio.create_task(self._publisher_loop(),name="telegram-outbox")
        self.oi_task=asyncio.create_task(self._refresh_oi_loop(),name="live-oi-poller")
        self.derivatives_task=asyncio.create_task(self._refresh_derivatives_history_loop(),name="historical-derivatives-refresh")
        interval=float(os.getenv("SIGNAL_EVALUATION_INTERVAL_S","300"))
        cycle=0; health_window_cycles=0; health_window_candidates=0; health_window_signals=0
        last_health_report=None
        while not self.stop_event.is_set():
            if self.cache.ws_task.done(): raise RuntimeError("market WebSocket consumer stopped")
            cycle+=1; cycle_started=time.monotonic()
            self._cycle_candidates=0; self._cycle_signals=0; ready_count=0
            logger.info("main_loop_cycle_started",extra={"context":{"cycle":cycle,"symbols":len(self.symbols),"interval_s":interval}})
            stamp=now_ms(); news=self.news_engine.build_news_state(stamp)
            for symbol in self.symbols:
                if self.stop_event.is_set(): break
                check=self._snapshot_check_context(symbol,now_ms())
                logger.info("snapshot_check",extra={"context":check})
                if check["ready"]: ready_count+=1
                try: await self.evaluate_symbol(self.cache.get_snapshot(symbol),news)
                except SnapshotIncompleteError as exc: self._log_snapshot_incomplete(symbol,exc)
                except Exception as exc: logger.exception("symbol evaluation failed; no signal published",extra={"context":{"symbol":symbol,"error":str(exc)}})
            cycle_candidates=self._cycle_candidates; cycle_signals=self._cycle_signals
            health_window_cycles+=1; health_window_candidates+=cycle_candidates; health_window_signals+=cycle_signals
            duration_ms=int((time.monotonic()-cycle_started)*1000)
            logger.info("main_loop_tick",extra={"context":{"cycle":cycle,"duration_ms":duration_ms,
                "ready":ready_count,"total":len(self.symbols),"candidates":cycle_candidates,"signals":cycle_signals}})
            now_mono=time.monotonic()
            if last_health_report is None or now_mono-last_health_report>=300.0:
                ws=getattr(self.cache,"ws",None)
                reconnects=getattr(ws,"reconnect_count_total",getattr(ws,"_reconnect_count_total",0)) if ws is not None else 0
                logger.info("loop_health",extra={"context":{"cycles":cycle,"cycles_last_5min":health_window_cycles,
                    "candidates_last_5min":health_window_candidates,"signals_last_5min":health_window_signals,
                    "ws_reconnects_total":reconnects,"ws_connected":bool(getattr(ws,"is_connected",False)),
                    "snapshots_ready":ready_count,"snapshot_total":len(self.symbols)}})
                health_window_cycles=0; health_window_candidates=0; health_window_signals=0
                last_health_report=now_mono
            try: await asyncio.wait_for(self.stop_event.wait(),timeout=interval)
            except asyncio.TimeoutError: pass

    async def shutdown(self):
        async with self._close_lock:
            if self._closed: return
            self._closed=True
            if self.stop_event: self.stop_event.set()
            for task in (self.news_task,self.oi_task,self.derivatives_task,self.report_task,self.cache.ws_task if self.cache else None):
                if task and not task.done(): task.cancel()
            await asyncio.gather(*(t for t in (self.news_task,self.oi_task,self.derivatives_task,self.cache.ws_task if self.cache else None) if t),return_exceptions=True)
            if self.outbox_task and not self.outbox_task.done():
                await self.outbox.join(); self.outbox.put_nowait(_STOP); await self.outbox.join(); await self.outbox_task
            if self.error_notifier is not None:
                try: await self.error_notifier.stop()
                except Exception as exc: logger.warning("error notifier shutdown failed",extra={"context":{"error_type":type(exc).__name__}})
            for obj in (self.news_collector,self.sender,self.repo,self.rest):
                if obj is not None:
                    try: await obj.close()
                    except Exception: logger.exception("component shutdown failed",extra={"context":{"component":type(obj).__name__}})
