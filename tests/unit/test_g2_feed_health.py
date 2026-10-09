from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g2_uses_configured_gap_threshold():
    s=_base_snapshot(feed_health={"x":FeedHealth("BTCUSDT","x",9_999_000,0,True)})
    r=veto.guard_g2_feed_health(s,_empty_news(),None,{"feed_gap_threshold_ms":500,"max_reconnects_per_window":5})
    assert not r.passed and r.action==GuardAction.BLOCK
