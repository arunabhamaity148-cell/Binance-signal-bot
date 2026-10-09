from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g11_short_circuits_after_g3_block():
    prior=[GuardResult("G3",False,GuardSeverity.CRITICAL,GuardAction.BLOCK,"thin")]
    assert veto.guard_g11_execution_quality(_base_snapshot(),_empty_news(),_sample_candidate(),VETO_CFG["g11_execution_quality"],symbol_tier="majors",prior_results=prior).passed
