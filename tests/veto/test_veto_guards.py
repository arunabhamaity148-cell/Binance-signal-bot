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



def test_only_seven_production_guards_are_configured():
    cfg = VETO_CFG
    assert {k for k, v in cfg.items() if isinstance(v, dict) and v.get("enabled")} == {
        "g1_data_integrity", "g2_feed_health", "g4_spread_explosion",
        "g5_oi_anomaly", "g6_funding_extreme", "g8_volatility_flash",
        "g9_btc_regime",
    }


def test_active_guard_functions_are_present():
    for name in (
        "guard_g1_data_integrity", "guard_g2_feed_health",
        "guard_g4_spread_explosion", "guard_g5_oi_anomaly",
        "guard_g6_funding_extreme", "guard_g8_volatility_flash",
        "guard_g9_btc_regime",
    ):
        assert callable(getattr(veto, name))
