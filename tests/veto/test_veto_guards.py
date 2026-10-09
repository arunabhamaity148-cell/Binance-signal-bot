from __future__ import annotations

from app.config import load_all
from app.core.math import OHLC
from app.core.models import (
    CandidateSignal,
    ChannelName,
    DerivativesState,
    Direction,
    FeedHealth,
    GuardAction,
    MarketSnapshot,
    NewsCategory,
    NewsDirection,
    NewsEvent,
    NewsSeverity,
    NewsState,
    OrderBookState,
    SymbolKlines,
    TimestampedValue,
)
from app.risk import veto

VETO_CFG = load_all().veto


def _empty_news(as_of=10_000_000):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _flat_bars(n, price, interval=300_000, start=0):
    return [
        OHLC(open=price, high=price + 1, low=price - 1, close=price, volume=100, close_time_ms=start + i * interval)
        for i in range(n)
    ]


def _base_snapshot(**overrides):
    defaults = dict(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=10_000_000,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=_flat_bars(250, 100.0))},
        orderbook=OrderBookState(
            symbol="BTCUSDT", best_bid=99.995, best_ask=100.005,
            bid_depth_5lvl_usd=500_000, ask_depth_5lvl_usd=500_000,
            event_ts_ms=10_000_000, received_ts_ms=10_000_000,
        ),
        taker_flow=None,
        derivatives=DerivativesState(
            symbol="BTCUSDT", funding_rate_history=[],
            open_interest_history_5m=[
                TimestampedValue(value=1_000_000 + i, event_ts_ms=10_000_000 - (10 - i) * 1000, received_ts_ms=10_000_000 - (10 - i) * 1000)
                for i in range(10)
            ],
            open_interest_history_15m=[], open_interest_history_1h=[],
            open_interest_history_1d=[
                TimestampedValue(value=1_000_000 + i, event_ts_ms=i * 86_400_000, received_ts_ms=10_000_000)
                for i in range(25)
            ],
            long_short_account_ratio_history=[], taker_long_short_ratio_history=[],
            premium_index_current=None,
        ),
        feed_health={
            "btcusdt@aggTrade": FeedHealth(symbol="BTCUSDT", stream="btcusdt@aggTrade", last_message_received_ts_ms=10_000_000, reconnect_count_window=0, is_connected=True),
        },
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _sample_candidate(strategy_source="S1"):
    return CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source=strategy_source,
        confidence=0.7, channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
        entry_low=100.0, entry_high=100.5, stop_loss=99.0, tp1=101, tp2=102, tp3=103, tp4=105,
        why_lines=("r1",), meta={"atr14": 1.0, "snapshot_version": "v1", "event_ts_ms": 10_000_000},
        event_ts_ms=10_000_000,
    )


# --- G1 ---

def test_g1_passes_on_valid_ordered_klines():
    snapshot = _base_snapshot()
    result = veto.guard_g1_data_integrity(snapshot, _empty_news(), None, VETO_CFG["g1_data_integrity"])
    assert result.passed


# --- G2 ---

def test_g2_passes_when_feed_healthy():
    snapshot = _base_snapshot()
    result = veto.guard_g2_feed_health(snapshot, _empty_news(), None, VETO_CFG["g2_feed_health"])
    assert result.passed


def test_g2_blocks_when_no_feed_health_data():
    snapshot = _base_snapshot(feed_health={})
    result = veto.guard_g2_feed_health(snapshot, _empty_news(), None, VETO_CFG["g2_feed_health"])
    assert not result.passed
    assert result.action == GuardAction.BLOCK


def test_g2_blocks_when_disconnected():
    snapshot = _base_snapshot(feed_health={
        "btcusdt@aggTrade": FeedHealth(symbol="BTCUSDT", stream="btcusdt@aggTrade", last_message_received_ts_ms=10_000_000, reconnect_count_window=0, is_connected=False),
    })
    result = veto.guard_g2_feed_health(snapshot, _empty_news(), None, VETO_CFG["g2_feed_health"])
    assert not result.passed


# --- G3 ---

def test_g3_passes_with_adequate_depth():
    snapshot = _base_snapshot()
    result = veto.guard_g3_depth_collapse(snapshot, _empty_news(), None, VETO_CFG["g3_depth_collapse"], symbol_tier="majors")
    assert result.passed


def test_g3_blocks_on_thin_depth():
    snapshot = _base_snapshot(orderbook=OrderBookState(
        symbol="BTCUSDT", best_bid=99.9, best_ask=100.1,
        bid_depth_5lvl_usd=100, ask_depth_5lvl_usd=100,
        event_ts_ms=10_000_000, received_ts_ms=10_000_000,
    ))
    result = veto.guard_g3_depth_collapse(snapshot, _empty_news(), None, VETO_CFG["g3_depth_collapse"], symbol_tier="majors")
    assert not result.passed
    assert result.action == GuardAction.BLOCK


def test_g3_blocks_on_missing_orderbook():
    snapshot = _base_snapshot(orderbook=None)
    result = veto.guard_g3_depth_collapse(snapshot, _empty_news(), None, VETO_CFG["g3_depth_collapse"], symbol_tier="majors")
    assert not result.passed


# --- G4 ---

def test_g4_passes_with_tight_spread():
    snapshot = _base_snapshot()
    result = veto.guard_g4_spread_explosion(snapshot, _empty_news(), None, VETO_CFG["g4_spread_explosion"], symbol_tier="majors")
    assert result.passed


def test_g4_blocks_on_wide_spread():
    snapshot = _base_snapshot(orderbook=OrderBookState(
        symbol="BTCUSDT", best_bid=95.0, best_ask=105.0,
        bid_depth_5lvl_usd=500_000, ask_depth_5lvl_usd=500_000,
        event_ts_ms=10_000_000, received_ts_ms=10_000_000,
    ))
    result = veto.guard_g4_spread_explosion(snapshot, _empty_news(), None, VETO_CFG["g4_spread_explosion"], symbol_tier="majors")
    assert not result.passed


def test_g4_defers_to_g10_on_crossed_book():
    snapshot = _base_snapshot(orderbook=OrderBookState(
        symbol="BTCUSDT", best_bid=101.0, best_ask=100.0,
        bid_depth_5lvl_usd=500_000, ask_depth_5lvl_usd=500_000,
        event_ts_ms=10_000_000, received_ts_ms=10_000_000,
    ))
    result = veto.guard_g4_spread_explosion(snapshot, _empty_news(), None, VETO_CFG["g4_spread_explosion"], symbol_tier="majors")
    assert result.passed  # G4 passes, defers crossed-book detection to G10


# --- G5 ---

def test_g5_passes_on_normal_oi_change():
    snapshot = _base_snapshot()
    result = veto.guard_g5_oi_anomaly(snapshot, _empty_news(), None, VETO_CFG["g5_oi_anomaly"])
    assert result.passed


def test_g5_blocks_on_oi_shock():
    oi_series = [
        TimestampedValue(value=1_000_000, event_ts_ms=9_700_000, received_ts_ms=9_700_000),
        TimestampedValue(value=2_000_000, event_ts_ms=10_000_000, received_ts_ms=10_000_000),  # +100% in one bar
    ]
    snapshot = _base_snapshot(derivatives=DerivativesState(
        symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=oi_series,
        open_interest_history_15m=[], open_interest_history_1h=[], open_interest_history_1d=[],
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None,
    ))
    result = veto.guard_g5_oi_anomaly(snapshot, _empty_news(), None, VETO_CFG["g5_oi_anomaly"])
    assert not result.passed


def test_g5_blocks_on_missing_derivatives():
    snapshot = _base_snapshot(derivatives=None)
    result = veto.guard_g5_oi_anomaly(snapshot, _empty_news(), None, VETO_CFG["g5_oi_anomaly"])
    assert not result.passed


# --- G6 ---

def test_g6_passes_when_funding_z_none():
    snapshot = _base_snapshot()
    result = veto.guard_g6_funding_extreme(snapshot, _empty_news(), None, VETO_CFG["g6_funding_extreme"], funding_z=None)
    assert result.passed


def test_g6_degrades_on_extreme_funding():
    snapshot = _base_snapshot()
    candidate = _sample_candidate(strategy_source="S1")
    result = veto.guard_g6_funding_extreme(snapshot, _empty_news(), candidate, VETO_CFG["g6_funding_extreme"], funding_z=4.0)
    assert not result.passed
    assert result.action == GuardAction.DEGRADE


def test_g6_does_not_double_penalize_s3():
    snapshot = _base_snapshot()
    candidate = _sample_candidate(strategy_source="S3")
    result = veto.guard_g6_funding_extreme(snapshot, _empty_news(), candidate, VETO_CFG["g6_funding_extreme"], funding_z=4.0)
    assert result.passed


# --- G7 ---

def test_g7_passes_with_no_news():
    snapshot = _base_snapshot()
    result = veto.guard_g7_news_shock(snapshot, _empty_news(), None, VETO_CFG["g7_news_shock"])
    assert result.passed


def test_g7_blocks_on_high_severity_news():
    event = NewsEvent(
        event_id="e1", source_name="binance_announcements", tier=1,
        canonical_url="https://example.com/1", domain="example.com",
        published_ts_ms=9_999_000, fetched_ts_ms=9_999_500, receipt_ts_ms=9_999_900,
        category=NewsCategory.HACK, direction=NewsDirection.BEARISH,
        entities=("BTCUSDT",), credibility_weight=1.0, confidence=0.9,
        severity=NewsSeverity.HIGH, content_hash="abc",
    )
    news = NewsState(as_of_ts_ms=10_000_000, active_events=(event,), unhealthy_categories=frozenset())
    snapshot = _base_snapshot()
    result = veto.guard_g7_news_shock(snapshot, news, None, VETO_CFG["g7_news_shock"])
    assert not result.passed


def test_g7_passes_with_low_severity_news():
    event = NewsEvent(
        event_id="e1", source_name="google_news_crypto", tier=3,
        canonical_url="https://example.com/1", domain="example.com",
        published_ts_ms=9_999_000, fetched_ts_ms=9_999_500, receipt_ts_ms=9_999_900,
        category=NewsCategory.OTHER, direction=NewsDirection.NEUTRAL,
        entities=("BTCUSDT",), credibility_weight=0.25, confidence=0.3,
        severity=NewsSeverity.LOW, content_hash="abc",
    )
    news = NewsState(as_of_ts_ms=10_000_000, active_events=(event,), unhealthy_categories=frozenset())
    snapshot = _base_snapshot()
    result = veto.guard_g7_news_shock(snapshot, news, None, VETO_CFG["g7_news_shock"])
    assert result.passed


# --- G8 ---

def test_g8_passes_on_normal_bar():
    bars = [
        OHLC(open=100.0, high=100.0 + (1.0 + ((i * 37) % 41) / 100.0),
             low=100.0 - (1.0 + ((i * 37) % 41) / 100.0), close=100.0,
             volume=100, close_time_ms=i * 300_000)
        for i in range(250)
    ]
    # Mild, non-flash variation makes the current ATR percentile meaningful
    # under the inclusive convention rather than a tied maximum.
    snapshot = _base_snapshot(
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
    )
    result = veto.guard_g8_volatility_flash(snapshot, _empty_news(), None, VETO_CFG["g8_volatility_flash"])
    assert result.passed


def test_g8_degrades_on_flash_bar():
    bars = _flat_bars(250, 100.0)
    flash_bar = OHLC(open=100, high=200, low=50, close=150, volume=1000, close_time_ms=bars[-1].close_time_ms + 300_000)
    bars = bars + [flash_bar]
    snapshot = _base_snapshot(klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)})
    result = veto.guard_g8_volatility_flash(snapshot, _empty_news(), None, VETO_CFG["g8_volatility_flash"])
    assert not result.passed
    assert result.action == GuardAction.DEGRADE


# --- G9 ---

def test_g9_passes_for_btc_itself():
    snapshot = _base_snapshot(symbol="BTCUSDT")
    candidate = _sample_candidate()
    result = veto.guard_g9_btc_regime(snapshot, _empty_news(), candidate, VETO_CFG["g9_btc_regime"], btc_trend_direction="down")
    assert result.passed


def test_g9_degrades_counter_trend_altcoin():
    snapshot = _base_snapshot(symbol="ETHUSDT")
    candidate = _sample_candidate()  # LONG
    result = veto.guard_g9_btc_regime(snapshot, _empty_news(), candidate, VETO_CFG["g9_btc_regime"], btc_trend_direction="down")
    assert not result.passed
    assert result.action == GuardAction.DEGRADE


def test_g9_passes_aligned_trend():
    snapshot = _base_snapshot(symbol="ETHUSDT")
    candidate = _sample_candidate()  # LONG
    result = veto.guard_g9_btc_regime(snapshot, _empty_news(), candidate, VETO_CFG["g9_btc_regime"], btc_trend_direction="up")
    assert result.passed


def test_g9_exempts_s3():
    snapshot = _base_snapshot(symbol="ETHUSDT")
    candidate = _sample_candidate(strategy_source="S3")
    result = veto.guard_g9_btc_regime(snapshot, _empty_news(), candidate, VETO_CFG["g9_btc_regime"], btc_trend_direction="down")
    assert result.passed


# --- G10 ---

def test_g10_passes_normal_book():
    snapshot = _base_snapshot()
    result = veto.guard_g10_orderbook_instability(snapshot, _empty_news(), None, VETO_CFG["g10_orderbook_instability"])
    assert result.passed


def test_g10_blocks_crossed_book():
    snapshot = _base_snapshot(orderbook=OrderBookState(
        symbol="BTCUSDT", best_bid=101.0, best_ask=100.0,
        bid_depth_5lvl_usd=500_000, ask_depth_5lvl_usd=500_000,
        event_ts_ms=10_000_000, received_ts_ms=10_000_000,
    ))
    result = veto.guard_g10_orderbook_instability(snapshot, _empty_news(), None, VETO_CFG["g10_orderbook_instability"])
    assert not result.passed


def test_g10_blocks_lopsided_book():
    snapshot = _base_snapshot(orderbook=OrderBookState(
        symbol="BTCUSDT", best_bid=99.9, best_ask=100.1,
        bid_depth_5lvl_usd=1_000_000, ask_depth_5lvl_usd=1_000,
        event_ts_ms=10_000_000, received_ts_ms=10_000_000,
    ))
    result = veto.guard_g10_orderbook_instability(snapshot, _empty_news(), None, VETO_CFG["g10_orderbook_instability"])
    assert not result.passed


# --- G11 ---

def test_g11_passes_with_no_candidate():
    snapshot = _base_snapshot()
    result = veto.guard_g11_execution_quality(
        snapshot, _empty_news(), None, VETO_CFG["g11_execution_quality"], symbol_tier="majors", prior_results=[]
    )
    assert result.passed


def test_g11_short_circuits_after_g3_block():
    from app.core.models import GuardResult, GuardSeverity

    prior = [GuardResult(guard_name="G3", passed=False, severity=GuardSeverity.CRITICAL, action=GuardAction.BLOCK, reason="thin depth")]
    snapshot = _base_snapshot()
    candidate = _sample_candidate()
    result = veto.guard_g11_execution_quality(
        snapshot, _empty_news(), candidate, VETO_CFG["g11_execution_quality"], symbol_tier="majors", prior_results=prior
    )
    assert result.passed  # short-circuited, no slippage estimation performed


# --- G12 ---

def test_g12_passes_with_no_candidate():
    snapshot = _base_snapshot()
    result = veto.guard_g12_self_consistency(snapshot, _empty_news(), None, VETO_CFG["g12_self_consistency"])
    assert result.passed


def test_g12_blocks_on_missing_meta_keys():
    snapshot = _base_snapshot()
    bad_candidate = CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
        channels=(ChannelName.LIQUIDITY,), entry_low=100, entry_high=100.5, stop_loss=99,
        tp1=101, tp2=102, tp3=103, tp4=105, why_lines=("r",), meta={}, event_ts_ms=10_000_000,
    )
    result = veto.guard_g12_self_consistency(snapshot, _empty_news(), bad_candidate, VETO_CFG["g12_self_consistency"])
    assert not result.passed


def test_g12_blocks_on_snapshot_version_mismatch():
    snapshot = _base_snapshot()
    candidate = CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
        channels=(ChannelName.LIQUIDITY,), entry_low=100, entry_high=100.5, stop_loss=99,
        tp1=101, tp2=102, tp3=103, tp4=105, why_lines=("r",),
        meta={"atr14": 1.0, "snapshot_version": "different-version", "event_ts_ms": 10_000_000},
        event_ts_ms=10_000_000,
    )
    result = veto.guard_g12_self_consistency(snapshot, _empty_news(), candidate, VETO_CFG["g12_self_consistency"])
    assert not result.passed


def test_g12_blocks_on_atr_mismatch():
    snapshot = _base_snapshot()  # flat bars -> true ATR should be small, near 2.0 (high-low range)
    candidate = CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
        channels=(ChannelName.LIQUIDITY,), entry_low=100, entry_high=100.5, stop_loss=99,
        tp1=101, tp2=102, tp3=103, tp4=105, why_lines=("r",),
        meta={"atr14": 9999.0, "snapshot_version": "v1", "event_ts_ms": 10_000_000},
        event_ts_ms=10_000_000,
    )
    result = veto.guard_g12_self_consistency(snapshot, _empty_news(), candidate, VETO_CFG["g12_self_consistency"])
    assert not result.passed


# --- G13 ---

def test_g13_passes_for_exempt_strategy():
    snapshot = _base_snapshot()
    candidate = _sample_candidate(strategy_source="S5")
    result = veto.guard_g13_oi_divergence(snapshot, _empty_news(), candidate, VETO_CFG["g13_oi_divergence"])
    assert result.passed


def test_g13_passes_with_no_candidate():
    snapshot = _base_snapshot()
    result = veto.guard_g13_oi_divergence(snapshot, _empty_news(), None, VETO_CFG["g13_oi_divergence"])
    assert result.passed


# --- G14 ---

def test_g14_passes_for_exempt_strategy_s1():
    snapshot = _base_snapshot()
    candidate = _sample_candidate(strategy_source="S1")
    result = veto.guard_g14_oi_stagnation(snapshot, _empty_news(), candidate, VETO_CFG["g14_oi_stagnation"])
    assert result.passed


# --- G15 ---

def test_g15_passes_for_non_applicable_strategy():
    snapshot = _base_snapshot()
    candidate = _sample_candidate(strategy_source="S1")  # G15 only applies_to S4
    result = veto.guard_g15_oi_percentile_extreme(snapshot, _empty_news(), candidate, VETO_CFG["g15_oi_percentile_extreme"])
    assert result.passed


def test_g15_blocks_s4_on_insufficient_history():
    snapshot = _base_snapshot()  # only 25 days of 1d OI history in fixture -> >= min_history_days(20) so let's make it short
    short_derivatives = DerivativesState(
        symbol="BTCUSDT", funding_rate_history=[], open_interest_history_5m=[
            TimestampedValue(value=1_000_000, event_ts_ms=10_000_000, received_ts_ms=10_000_000)
        ],
        open_interest_history_15m=[], open_interest_history_1h=[],
        open_interest_history_1d=[TimestampedValue(value=1_000_000, event_ts_ms=0, received_ts_ms=10_000_000)],  # only 1 day
        long_short_account_ratio_history=[], taker_long_short_ratio_history=[], premium_index_current=None,
    )
    snapshot = _base_snapshot(derivatives=short_derivatives)
    candidate = _sample_candidate(strategy_source="S4")
    result = veto.guard_g15_oi_percentile_extreme(snapshot, _empty_news(), candidate, VETO_CFG["g15_oi_percentile_extreme"])
    assert not result.passed
