from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g12_blocks_missing_candidate_metadata():
    c=_sample_candidate(); c=CandidateSignal(**{**c.__dict__,"meta":{}})
    assert not veto.guard_g12_self_consistency(_base_snapshot(),_empty_news(),c,VETO_CFG["g12_self_consistency"]).passed
