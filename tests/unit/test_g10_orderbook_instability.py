from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g10_blocks_crossed_book():
    s=_base_snapshot(orderbook=OrderBookState("BTCUSDT",101,100,500_000,500_000,10_000_000,10_000_000))
    assert not veto.guard_g10_orderbook_instability(s,_empty_news(),None,VETO_CFG["g10_orderbook_instability"]).passed
