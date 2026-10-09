from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g14_exempts_s1():
    assert veto.guard_g14_oi_stagnation(_base_snapshot(),_empty_news(),_sample_candidate("S1"),VETO_CFG["g14_oi_stagnation"]).passed
