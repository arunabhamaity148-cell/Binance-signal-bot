"""Offline JSONL backtest input harness (no network or live I/O).

Primary format: one JSON object per 5m row, oldest-first, with required
`ts_ms/open/high/low/close/volume`. Optional `orderbook`, `taker_flow`, and
`derivatives` objects apply to that bar index. Optional `timeframes` maps
`15m`, `1h`, `4h`, or `1d` to arrays of closed OHLCV records visible as of
that 5m row. These higher-timeframe records are merged by timestamp and
future-dated records are rejected.

Derivatives are per-row snapshots of complete-to-date histories using the
`DerivativesState` field names: funding_rate_history, open_interest_history_5m/15m/1h/1d,
long_short_account_ratio_history, taker_long_short_ratio_history, and optional
premium_index_current. Each point has value, event_ts_ms, received_ts_ms.
The per-row choice makes each replay index's available derivatives explicit
and prevents later samples from leaking into earlier snapshots.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import DataIntegrityError
from app.core.math import OHLC
from app.core.models import (DerivativesState, OrderBookState, TakerFlowState, TimestampedValue)

_DERIVATIVE_SERIES = (
    "funding_rate_history", "open_interest_history_5m", "open_interest_history_15m",
    "open_interest_history_1h", "open_interest_history_1d",
    "long_short_account_ratio_history", "taker_long_short_ratio_history",
)
_TIMEFRAMES = {"15m", "1h", "4h", "1d"}

@dataclass(frozen=True)
class ReplayData:
    bars: list[OHLC]
    orderbook_at_index: dict[int, OrderBookState]
    taker_flow_at_index: dict[int, TakerFlowState]
    derivatives_at_index: dict[int, DerivativesState] = field(default_factory=dict)
    all_bars: dict[str, list[OHLC]] = field(default_factory=dict)

def _parse_bar(row: dict, *, path: Path, lineno: int) -> OHLC:
    required = ("ts_ms", "open", "high", "low", "close", "volume")
    missing = [key for key in required if key not in row]
    if missing:
        raise DataIntegrityError(f"{path}:{lineno}: row missing required fields {missing}")
    try:
        return OHLC(open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                    close=float(row["close"]), volume=float(row["volume"]), close_time_ms=int(row["ts_ms"]))
    except (TypeError, ValueError) as exc:
        raise DataIntegrityError(f"{path}:{lineno}: invalid OHLC/ts values: {exc}") from exc

def _parse_series(raw: object, *, key: str, as_of_ts_ms: int, path: Path, lineno: int) -> list[TimestampedValue]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DataIntegrityError(f"{path}:{lineno}: derivatives.{key} must be a list")
    points: list[TimestampedValue] = []
    previous = -1
    for index, point in enumerate(raw):
        try:
            event_ts = int(point["event_ts_ms"]); received_ts = int(point["received_ts_ms"]); value = float(point["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataIntegrityError(f"{path}:{lineno}: invalid derivatives.{key}[{index}]: {exc}") from exc
        if event_ts < previous or event_ts > as_of_ts_ms or received_ts > as_of_ts_ms:
            raise DataIntegrityError(f"{path}:{lineno}: derivatives.{key}[{index}] is unsorted or future-dated")
        previous = event_ts
        points.append(TimestampedValue(value=value, event_ts_ms=event_ts, received_ts_ms=received_ts))
    return points

def load_bars_jsonl(path: str | Path, *, symbol: str) -> ReplayData:
    p=Path(path)
    if not p.exists(): raise FileNotFoundError(f"backtest data file not found: {p}")
    bars=[]; orderbooks={}; taker_flows={}; derivatives={}; higher={tf:{} for tf in _TIMEFRAMES}; prev_ts=None
    with p.open("r",encoding="utf-8") as fh:
        for lineno,line in enumerate(fh,1):
            line=line.strip()
            if not line: continue
            try: row=json.loads(line)
            except json.JSONDecodeError as exc: raise DataIntegrityError(f"{p}:{lineno}: malformed JSON: {exc}") from exc
            if not isinstance(row,dict): raise DataIntegrityError(f"{p}:{lineno}: JSONL row must be an object")
            bar=_parse_bar(row,path=p,lineno=lineno); ts=bar.close_time_ms
            if prev_ts is not None and ts<=prev_ts: raise DataIntegrityError(f"{p}:{lineno}: out-of-order or duplicate ts_ms {ts} <= previous {prev_ts}")
            prev_ts=ts; idx=len(bars); bars.append(bar)
            if "orderbook" in row:
                ob=row["orderbook"]; needed=("best_bid","best_ask","bid_depth_5lvl_usd","ask_depth_5lvl_usd")
                missing=[k for k in needed if k not in ob]
                if missing: raise DataIntegrityError(f"{p}:{lineno}: orderbook row missing fields {missing}")
                orderbooks[idx]=OrderBookState(symbol=symbol,best_bid=float(ob["best_bid"]),best_ask=float(ob["best_ask"]),bid_depth_5lvl_usd=float(ob["bid_depth_5lvl_usd"]),ask_depth_5lvl_usd=float(ob["ask_depth_5lvl_usd"]),event_ts_ms=ts,received_ts_ms=ts)
            if "taker_flow" in row:
                tf=row["taker_flow"]; needed=("taker_buy_base_last_bars","total_volume_last_bars"); missing=[k for k in needed if k not in tf]
                if missing: raise DataIntegrityError(f"{p}:{lineno}: taker_flow row missing fields {missing}")
                order=[float(x) for x in tf["taker_buy_base_last_bars"]]; total=[float(x) for x in tf["total_volume_last_bars"]]
                if len(order)!=len(total) or not order: raise DataIntegrityError(f"{p}:{lineno}: taker_flow arrays must be non-empty and equal length")
                taker_flows[idx]=TakerFlowState(symbol=symbol,taker_buy_base_last_bars=order,total_volume_last_bars=total,event_ts_ms=ts,received_ts_ms=ts)
            if "derivatives" in row:
                raw=row["derivatives"]
                if not isinstance(raw,dict): raise DataIntegrityError(f"{p}:{lineno}: derivatives must be an object")
                series={k:_parse_series(raw.get(k,[]),key=k,as_of_ts_ms=ts,path=p,lineno=lineno) for k in _DERIVATIVE_SERIES}
                premium_raw=raw.get("premium_index_current")
                premium=None
                if premium_raw is not None:
                    parsed=_parse_series([premium_raw],key="premium_index_current",as_of_ts_ms=ts,path=p,lineno=lineno)
                    premium=parsed[0] if parsed else None
                derivatives[idx]=DerivativesState(symbol=symbol,**series,premium_index_current=premium)
            frames=row.get("timeframes",{})
            if not isinstance(frames,dict): raise DataIntegrityError(f"{p}:{lineno}: timeframes must be an object")
            for tf,records in frames.items():
                if tf not in _TIMEFRAMES or not isinstance(records,list): raise DataIntegrityError(f"{p}:{lineno}: unsupported timeframe or non-list: {tf}")
                for record in records:
                    higher_bar=_parse_bar(record,path=p,lineno=lineno)
                    if higher_bar.close_time_ms>ts: raise DataIntegrityError(f"{p}:{lineno}: future {tf} bar leaks into replay row")
                    prior=higher[tf].get(higher_bar.close_time_ms)
                    if prior is not None and prior!=higher_bar: raise DataIntegrityError(f"{p}:{lineno}: conflicting {tf} bar at {higher_bar.close_time_ms}")
                    higher[tf][higher_bar.close_time_ms]=higher_bar
    if not bars: raise DataIntegrityError(f"{p}: no valid bar rows found")
    all_bars={"5m":bars,**{tf:[items[t] for t in sorted(items)] for tf,items in higher.items()}}
    return ReplayData(bars=bars,orderbook_at_index=orderbooks,taker_flow_at_index=taker_flows,derivatives_at_index=derivatives,all_bars=all_bars)

def write_bars_jsonl(path: str | Path, bars: list[OHLC]) -> None:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",encoding="utf-8") as fh:
        for bar in bars:
            fh.write(json.dumps({"ts_ms":bar.close_time_ms,"open":bar.open,"high":bar.high,"low":bar.low,"close":bar.close,"volume":bar.volume})+"\n")
