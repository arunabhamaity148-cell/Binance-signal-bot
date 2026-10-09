from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g9_blocks_countertrend_only_as_degrade():
    r=veto.guard_g9_btc_regime(_base_snapshot(symbol="ETHUSDT"),_empty_news(),_sample_candidate("S1"),VETO_CFG["g9_btc_regime"],btc_trend_direction="down")
    assert r.action==GuardAction.DEGRADE
