from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g7_blocks_high_news():
    e=NewsEvent("e","x",1,"https://x","x",1,1,1,NewsCategory.HACK,NewsDirection.BEARISH,("BTCUSDT",),1,.9,NewsSeverity.HIGH,"h")
    assert not veto.guard_g7_news_shock(_base_snapshot(),NewsState(10_000_000,(e,),frozenset()),None,VETO_CFG["g7_news_shock"]).passed
