from __future__ import annotations
from dataclasses import replace
from app.config import load_all
from app.backtest.engine import _build_snapshot_at_index, generate_candidates_at_snapshot
from app.core.models import NewsState
from app.risk.veto_engine import run_veto_engine
from tests.integration.test_backtest_engine_e2e import _build_fixture

def test_g2_blocks_candidate_when_feed_health_is_unavailable():
    cfg=load_all(); bars,trigger_index,ob,tf,deriv=_build_fixture(
        penetration_atr_mult=0.6,continuation_step=80.0,continuation_bars=40)
    ts=bars[trigger_index].close_time_ms
    pair=cfg.pair_config("BTCUSDT")
    snapshot=_build_snapshot_at_index(symbol="BTCUSDT",all_bars={"5m":bars},index_per_timeframe={"5m":trigger_index},
        as_of_ts_ms=ts,price_tick=pair["price_tick"],qty_step=pair["qty_step"],min_qty=pair["min_qty"],
        fee_maker_bps=pair["fee_maker_bps"],fee_taker_bps=pair["fee_taker_bps"],min_candles=60,
        orderbook=ob,taker_flow=tf,derivatives=deriv)
    assert snapshot is not None
    candidates=generate_candidates_at_snapshot(snapshot,NewsState(ts,()),cfg.strategy)
    assert candidates
    unhealthy=replace(snapshot,feed_health={})
    outcome=run_veto_engine(snapshot=unhealthy,news_state=NewsState(ts,()),candidate=candidates[0],
        veto_cfg=cfg.veto,symbol_tier="majors",funding_z=None,btc_trend_direction=None)
    g2=next(result for result in outcome.guard_results if result.guard_name=="G2")
    assert not g2.passed
    assert outcome.veto_state.value=="BLOCK"
