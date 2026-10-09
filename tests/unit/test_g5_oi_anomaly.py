from __future__ import annotations
from tests.veto.test_veto_guards import _base_snapshot, _empty_news, _sample_candidate, _flat_bars, VETO_CFG, FeedHealth, OrderBookState, DerivativesState, TimestampedValue, GuardAction, NewsEvent, NewsState, NewsCategory, NewsDirection, NewsSeverity, OHLC, SymbolKlines , CandidateSignal
from app.risk import veto
from app.core.models import GuardResult, GuardSeverity

def test_g5_blocks_single_bar_oi_shock():
    d=_base_snapshot().derivatives; d=DerivativesState(d.symbol,d.funding_rate_history,[TimestampedValue(1_000_000,9_000_000,9_000_000),TimestampedValue(2_000_000,10_000_000,10_000_000)],d.open_interest_history_15m,d.open_interest_history_1h,d.open_interest_history_1d,d.long_short_account_ratio_history,d.taker_long_short_ratio_history,d.premium_index_current)
    assert not veto.guard_g5_oi_anomaly(_base_snapshot(derivatives=d),_empty_news(),None,VETO_CFG["g5_oi_anomaly"]).passed
