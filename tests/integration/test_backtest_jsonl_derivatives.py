from __future__ import annotations
import json
from pathlib import Path
import pytest
from app.backtest.harness import load_bars_jsonl
from app.core.errors import DataIntegrityError
from app.core.models import DerivativesState

_FIELDS=("funding_rate_history","open_interest_history_5m","open_interest_history_15m",
         "open_interest_history_1h","open_interest_history_1d",
         "long_short_account_ratio_history","taker_long_short_ratio_history")
def _point(ts,value): return {"value":value,"event_ts_ms":ts,"received_ts_ms":ts}
def _write(path,rows):
    path.write_text("".join(json.dumps(row)+"\n" for row in rows),encoding="utf-8")
def _row(ts,derivatives):
    return {"ts_ms":ts,"open":100,"high":102,"low":99,"close":101,"volume":10,"derivatives":derivatives}

def test_jsonl_loader_builds_derivatives_histories(tmp_path):
    ts=1_000_000
    derivatives={key:[_point(ts-2000,1.0),_point(ts-1000,2.0)] for key in _FIELDS}
    derivatives["premium_index_current"]=_point(ts,0.0001)
    path=tmp_path/"replay.jsonl"; _write(path,[_row(ts,derivatives)])
    replay=load_bars_jsonl(path,symbol="BTCUSDT")
    state=replay.derivatives_at_index[0]
    assert isinstance(state,DerivativesState)
    assert state.funding_rate_history[-1].value==2.0
    assert state.open_interest_history_5m[-1].value==2.0
    assert state.open_interest_history_1d[-1].value==2.0
    assert state.long_short_account_ratio_history[-1].value==2.0
    assert state.taker_long_short_ratio_history[-1].value==2.0
    assert state.premium_index_current.value==0.0001

def test_jsonl_loader_rejects_future_derivatives_sample(tmp_path):
    path=tmp_path/"future.jsonl"; _write(path,[_row(1000,{"funding_rate_history":[_point(1001,0.1)]})])
    with pytest.raises(DataIntegrityError,match="future-dated"):
        load_bars_jsonl(path,symbol="BTCUSDT")

# Verified strategy replay path: strategy fixtures are serialized to the JSONL
# harness format, loaded back, and evaluated from the reconstructed snapshot.
def _serialize_snapshot(snapshot,path):
    from app.core.models import DerivativesState
    bars5=snapshot.klines["5m"].bars
    higher={tf:sk.bars for tf,sk in snapshot.klines.items() if tf!="5m"}
    target=bars5[-1].close_time_ms
    # Align fixture time axes to one replay endpoint without changing OHLC values.
    shifted={tf:[type(b)(b.open,b.high,b.low,b.close,b.volume,target-(len(seq)-1-i)*{"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}[tf]) for i,b in enumerate(seq)] for tf,seq in higher.items()}
    derivative=snapshot.derivatives
    def points(series):
        if derivative is None: return []
        shift=target-max((x.event_ts_ms for x in series),default=target)
        return [{"value":x.value,"event_ts_ms":x.event_ts_ms+shift,"received_ts_ms":min(target,x.received_ts_ms+shift)} for x in sorted(series,key=lambda x:x.event_ts_ms)]
    rows=[]
    for i,bar in enumerate(bars5):
        ts=target-(len(bars5)-1-i)*300000
        row={"ts_ms":ts,"open":bar.open,"high":bar.high,"low":bar.low,"close":bar.close,"volume":bar.volume}
        row["timeframes"]={tf:[{"ts_ms":b.close_time_ms,"open":b.open,"high":b.high,"low":b.low,"close":b.close,"volume":b.volume} for b in items if b.close_time_ms<=ts] for tf,items in shifted.items()}
        if i==len(bars5)-1:
            if snapshot.orderbook is not None:
                o=snapshot.orderbook; row["orderbook"]={"best_bid":o.best_bid,"best_ask":o.best_ask,"bid_depth_5lvl_usd":o.bid_depth_5lvl_usd,"ask_depth_5lvl_usd":o.ask_depth_5lvl_usd}
            if snapshot.taker_flow is not None:
                t=snapshot.taker_flow; row["taker_flow"]={"taker_buy_base_last_bars":t.taker_buy_base_last_bars,"total_volume_last_bars":t.total_volume_last_bars}
            if derivative is not None:
                d={key:points(getattr(derivative,key)) for key in _FIELDS}
                d["premium_index_current"]=None if derivative.premium_index_current is None else {"value":derivative.premium_index_current.value,"event_ts_ms":target,"received_ts_ms":target}
                row["derivatives"]=d
        rows.append(row)
    _write(path,rows)

def test_s2_s3_s4_s5_can_trigger_from_jsonl_replay(tmp_path):
    from app.config import load_all
    from app.backtest.engine import _build_snapshot_at_index
    from app.core.models import NewsState
    from app.strategies.s2_volatility_compression import S2VolatilityCompression
    from app.strategies.s3_funding_crowding import S3FundingCrowding
    from app.strategies.s4_oi_trend import S4OiTrend
    from app.strategies.s5_oi_regime import S5OiRegime
    from tests.strategies.test_s2_volatility_compression import build_realistic_s2_long_snapshot
    from tests.strategies.test_s3_funding_crowding import build_realistic_s3_long_reversal_snapshot
    from tests.strategies.test_s5_oi_regime import build_realistic_s5_long_snapshot
    from tests.integration.test_backtest_s4_jsonl_fixture import build_s4_trigger_snapshot
    cases=[("S2",S2VolatilityCompression(),build_realistic_s2_long_snapshot()),
           ("S3",S3FundingCrowding(),build_realistic_s3_long_reversal_snapshot()),
           ("S4",S4OiTrend(),build_s4_trigger_snapshot()),
           ("S5",S5OiRegime(),build_realistic_s5_long_snapshot())]
    cfg=load_all()
    for name,strategy,fixture in cases:
        path=tmp_path/f"{name}.jsonl"; _serialize_snapshot(fixture,path)
        replay=load_bars_jsonl(path,symbol=fixture.symbol)
        i=len(replay.bars)-1; ts=replay.bars[i].close_time_ms; pair=cfg.pair_config(fixture.symbol)
        inds={tf:max((j for j,b in enumerate(series) if b.close_time_ms<=ts),default=-1) for tf,series in replay.all_bars.items() if any(b.close_time_ms<=ts for b in series)}
        snapshot=_build_snapshot_at_index(symbol=fixture.symbol,all_bars=replay.all_bars,index_per_timeframe=inds,as_of_ts_ms=ts,
            price_tick=pair["price_tick"],qty_step=pair["qty_step"],min_qty=pair["min_qty"],fee_maker_bps=pair["fee_maker_bps"],
            fee_taker_bps=pair["fee_taker_bps"],min_candles=cfg.strategy["common"]["min_candles"],
            orderbook=replay.orderbook_at_index.get(i),taker_flow=replay.taker_flow_at_index.get(i),derivatives=replay.derivatives_at_index.get(i))
        assert snapshot is not None, name
        got=strategy.evaluate(snapshot,NewsState(ts,()),cfg.strategy)
        assert any(candidate.strategy_source==name for candidate in got),name
