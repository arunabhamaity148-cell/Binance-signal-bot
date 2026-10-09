"""Synthetic no-network fixtures and shared pipeline runner for smoke/canary scripts."""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.backtest.engine import generate_candidates_at_snapshot, grade_candidates
from app.config import AppConfig
from app.core.math import OHLC
from app.core.models import (DerivativesState,FeedHealth,MarketSnapshot,NewsState,OrderBookState,SymbolKlines,TakerFlowState,TimestampedValue)
from app.risk.regime_detector import MarketRegime, detect_regime
from app.risk.risk_engine import combined_sizing_multiplier, compute_position_size
from app.risk.veto_engine import run_veto_engine
from app.signals.signal_engine import build_final_signal

@dataclass
class PipelineRun:
    symbol: str
    evaluated: bool
    candidates: int
    vetoes_blocked: int
    built_signals: list
    eligible_signals: list
    errors: list[str]


def build_offline_snapshot(cfg: AppConfig, symbol: str) -> MarketSnapshot:
    """Construct a deterministic synthetic S1 sweep/reclaim snapshot, never live market data."""
    pair=cfg.pair_config(symbol); price=100_000.0; now=1_800_000_000_000; iv=300_000; n=300
    rng=random.Random(20261008); t0=now-(n+5)*iv; bars=[]
    for i in range(n-8):
        close=price+(40 if i%2==0 else -40); half=rng.uniform(70,250)
        bars.append(OHLC(price,price+half,price-half,close,100.0,t0+i*iv))
    start=len(bars)
    for j in range(3): bars.append(OHLC(price,price+100,price-100,price,100.0,t0+(start+j)*iv))
    bars.append(OHLC(price,price+100,price-150,price-20,100.0,t0+(start+3)*iv))
    from app.core.math import wilder_atr
    atr=wilder_atr(bars,14)
    bars.append(OHLC(price,price+100+0.60*atr,price-60,price+100-0.14*atr,300.0,t0+(start+4)*iv))
    as_of=bars[-1].close_time_ms+1_000
    def flat(count, interval):
        begin=as_of-(count+1)*interval
        return [OHLC(price,price+1,price-1,price,100.0,begin+i*interval) for i in range(count)]
    def uptrend(count, interval):
        begin=as_of-(count+1)*interval; out=[]
        for i in range(count):
            base=price+i*10; high=base+1; low=base-1
            if interval==14_400_000 and i in (count-17,count-9): high=base+(60 if i==count-9 else 50)
            if interval==14_400_000 and i in (count-13,count-5): low=base-40
            out.append(OHLC(base,high,low,base,100.0,begin+i*interval))
        return out
    ob=OrderBookState(symbol,price-0.05,price+0.05,2_000_000.0,2_000_000.0,as_of,as_of)
    taker=TakerFlowState(symbol,[8.0,8.0,8.0],[10.0,10.0,10.0],as_of,as_of)
    latest=as_of-30_000
    oi5=[TimestampedValue(1_000_000+i*20,latest-(59-i)*iv,latest-(59-i)*iv+500) for i in range(60)]
    oi1d=[TimestampedValue(1_000_000+i*500,as_of-(30-i)*86_400_000,as_of) for i in range(30)]
    oi1h=[TimestampedValue(1_000_000,as_of-3_600_000,as_of),TimestampedValue(1_020_000,as_of,as_of)]
    deriv=DerivativesState(symbol,[],oi5,[],oi1h,oi1d,[],[],None)
    feed={f"{symbol.lower()}@aggTrade":FeedHealth(symbol,f"{symbol.lower()}@aggTrade",as_of-500,0,True)}
    return MarketSnapshot(f"offline-smoke-{symbol}-{as_of}",symbol,as_of,
        {"5m":SymbolKlines(symbol,"5m",bars),"15m":SymbolKlines(symbol,"15m",flat(80,900_000)),
         "1h":SymbolKlines(symbol,"1h",uptrend(80,3_600_000)),"4h":SymbolKlines(symbol,"4h",uptrend(80,14_400_000))},
        ob,taker,deriv,feed,float(pair["price_tick"]),float(pair["qty_step"]),float(pair["min_qty"]),
        float(pair["fee_maker_bps"]),float(pair["fee_taker_bps"]))


def run_offline_pipeline(cfg: AppConfig, symbol: str, assumed_equity_usd: float) -> PipelineRun:
    """Run strategy -> consensus -> veto -> sizing -> signal construction on the synthetic fixture."""
    snap=build_offline_snapshot(cfg,symbol); news=NewsState(snap.as_of_ts_ms,(),frozenset())
    regime, metrics = detect_regime(snap, None)
    candidates=generate_candidates_at_snapshot(snap,news,cfg.strategy,regime=regime)
    graded=grade_candidates(candidates,cfg.strategy["consensus"])
    built=[]; eligible=[]; blocked=0; errors=[]
    for (sym,_direction),(grade,confidence,group) in graded.items():
        if grade is None: continue
        candidate=group[0]
        outcome=run_veto_engine(snapshot=snap,news_state=news,candidate=candidate,veto_cfg=cfg.veto,
            symbol_tier=cfg.symbol_tier(sym),funding_z=None,btc_trend_direction="up")
        if outcome.veto_state.value!="PASS": blocked+=1; continue
        try:
            pair=cfg.pair_config(sym)
            multiplier=combined_sizing_multiplier(regime.value, metrics.atr_percentile)
            size=compute_position_size(assumed_equity_usd=assumed_equity_usd,
                risk_per_trade_pct=cfg.risk["risk_per_trade_pct"],entry_price=(candidate.entry_low+candidate.entry_high)/2,
                stop_loss=candidate.stop_loss,qty_step=pair["qty_step"],fee_maker_bps=pair["fee_maker_bps"],
                fee_taker_bps=pair["fee_taker_bps"],depth_usd=min(snap.orderbook.bid_depth_5lvl_usd,snap.orderbook.ask_depth_5lvl_usd),
                sizing_multiplier=multiplier)
            signal=build_final_signal(candidate=candidate,snapshot=snap,grade=grade,confidence_weighted=confidence,
                veto_outcome=outcome,size_units_advisory=size.qty,notional_usd_advisory=size.notional_usd,
                expiry_per_grade=cfg.risk["expiry_per_grade"],created_ts_ms=snap.as_of_ts_ms,
                sizing_multiplier=multiplier,regime=regime.value,
                htf_confluence=False)
            built.append(signal)
            if signal.rr_tp2>=cfg.risk["min_rr_tp2"]: eligible.append(signal)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    return PipelineRun(symbol,True,len(candidates),blocked,built,eligible,errors)
