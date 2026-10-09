from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g3_blocks_below_symbol_tier_minimum():
    s=_base_snapshot(orderbook=OrderBookState("BTCUSDT",99,101,199_999,500_000,10_000_000,10_000_000))
    r=veto.guard_g3_depth_collapse(s,_empty_news(),None,VETO_CFG["g3_depth_collapse"],symbol_tier="majors")
    assert not r.passed
