from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g8_flash_bar_degrades():
    bars=_flat_bars(250,100)+[OHLC(100,200,50,150,1000,250*300_000)]
    s=_base_snapshot(klines={"5m":SymbolKlines("BTCUSDT","5m",bars)})
    assert veto.guard_g8_volatility_flash(s,_empty_news(),None,VETO_CFG["g8_volatility_flash"]).action==GuardAction.DEGRADE
