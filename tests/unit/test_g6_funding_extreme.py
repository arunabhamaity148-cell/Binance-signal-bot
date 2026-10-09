from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g6_degrades_non_s3_extreme_funding():
    r=veto.guard_g6_funding_extreme(_base_snapshot(),_empty_news(),_sample_candidate("S1"),VETO_CFG["g6_funding_extreme"],funding_z=4)
    assert r.action==GuardAction.DEGRADE and not r.passed
