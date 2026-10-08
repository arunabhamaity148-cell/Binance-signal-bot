from __future__ import annotations
import app.backtest.engine as engine
from app.backtest.costs import compute_cost_breakdown as real_compute_cost_breakdown
from tests.integration.test_backtest_engine_e2e import _build_fixture, _run

def test_backtest_passes_default_assumed_latency_into_cost_breakdown(monkeypatch):
    seen=[]
    def recording_cost(**kwargs):
        seen.append(kwargs["latency_s"])
        return real_compute_cost_breakdown(**kwargs)
    monkeypatch.setattr(engine,"compute_cost_breakdown",recording_cost)
    bars,_,ob,tf,deriv=_build_fixture(penetration_atr_mult=0.6,continuation_step=80.0,continuation_bars=40)
    trades=_run(bars,ob,tf,deriv)
    assert trades
    assert seen and all(value==2.0 for value in seen)

def test_latency_cost_is_nonzero_at_default_assumption():
    cost=real_compute_cost_breakdown(entry_price=100.0,stop_price=98.0,fee_maker_bps=2.0,
        fee_taker_bps=5.0,notional_usd=1000.0,depth_usd=100000.0,latency_s=2.0)
    assert cost.entry_latency_slippage>0
