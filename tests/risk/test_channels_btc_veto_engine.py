from __future__ import annotations

import pytest

from app.config import load_all
from app.core.math import OHLC
from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    GuardAction,
    MarketSnapshot,
    NewsState,
    SymbolKlines,
    VetoState,
)
from app.risk.btc_regime import compute_btc_trend_direction
from app.risk.channels import STRATEGY_CHANNELS, channels_for_strategy
from app.risk.veto_engine import run_veto_engine

CFG = load_all()


def _empty_news(as_of=10_000_000):
    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())


def _trend_bars(n, start_price, step, interval):
    bars = []
    price = start_price
    for i in range(n):
        o, c = price, price + step
        bars.append(OHLC(open=o, high=max(o, c) + 0.5, low=min(o, c) - 0.5, close=c, volume=100, close_time_ms=i * interval))
        price = c
    return bars


def _snapshot_with_1h(bars_1h):
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=10_000_000,
        klines={"1h": SymbolKlines(symbol="BTCUSDT", timeframe="1h", bars=bars_1h)},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


# --- channels ---

def test_channels_defined_for_all_five_strategies():
    assert set(STRATEGY_CHANNELS.keys()) == {"S1", "S2", "S3", "S4", "S5"}


def test_channels_for_strategy_returns_tuple_of_channelnames():
    for sid in STRATEGY_CHANNELS:
        channels = channels_for_strategy(sid)
        assert len(channels) >= 1
        assert all(isinstance(c, ChannelName) for c in channels)


def test_channels_for_unknown_strategy_raises():
    with pytest.raises(KeyError):
        channels_for_strategy("S9")


def test_s1_uses_liquidity_and_taker_flow():
    assert set(channels_for_strategy("S1")) == {ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW}


# --- btc_regime ---

def test_btc_trend_none_on_insufficient_history():
    snap = _snapshot_with_1h(_trend_bars(10, 100, 1, 3_600_000))
    assert compute_btc_trend_direction(snap, CFG.strategy["s4_oi_trend"]) is None


def test_btc_trend_up_on_rising_series():
    snap = _snapshot_with_1h(_trend_bars(100, 100, 1, 3_600_000))
    assert compute_btc_trend_direction(snap, CFG.strategy["s4_oi_trend"]) == "up"


def test_btc_trend_down_on_falling_series():
    snap = _snapshot_with_1h(_trend_bars(100, 500, -1, 3_600_000))
    assert compute_btc_trend_direction(snap, CFG.strategy["s4_oi_trend"]) == "down"


def test_btc_trend_flat_on_constant_series():
    flat = [
        OHLC(open=100, high=101, low=99, close=100, volume=100, close_time_ms=i * 3_600_000)
        for i in range(100)
    ]
    snap = _snapshot_with_1h(flat)
    assert compute_btc_trend_direction(snap, CFG.strategy["s4_oi_trend"]) == "flat"


# --- veto_engine ---

def _minimal_snapshot_no_feed():
    bars = [OHLC(open=100, high=101, low=99, close=100, volume=1, close_time_ms=i * 300_000) for i in range(260)]
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=10_000_000,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
        orderbook=None, taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def test_veto_engine_blocks_and_short_circuits_on_g2():
    """No feed health data -> G2 hard blocks; later guards must not run."""
    outcome = run_veto_engine(
        snapshot=_minimal_snapshot_no_feed(), news_state=_empty_news(), candidate=None,
        veto_cfg=CFG.veto, symbol_tier="majors",
    )
    assert outcome.veto_state == VetoState.BLOCK
    names = [g.guard_name for g in outcome.guard_results]
    assert names == ["G1", "G2"]
    assert "G2" in outcome.veto_reason


def test_veto_engine_guard_exception_becomes_block(monkeypatch):
    """An exception raised inside a guard must become a BLOCK, never a
    silent pass (spec section 12)."""
    from app.risk import veto

    def boom(*args, **kwargs):
        raise RuntimeError("guard blew up")

    monkeypatch.setattr(veto, "guard_g1_data_integrity", boom)
    outcome = run_veto_engine(
        snapshot=_minimal_snapshot_no_feed(), news_state=_empty_news(), candidate=None,
        veto_cfg=CFG.veto, symbol_tier="majors",
    )
    assert outcome.veto_state == VetoState.BLOCK
    assert "guard_exception:G1" in outcome.veto_reason


def test_veto_engine_execution_order_matches_spec(monkeypatch):
    """Record the order of the seven enabled production guards."""
    from app.core.models import GuardResult, GuardSeverity
    from app.risk import veto

    calls = []

    def make_passer(name):
        def passer(*args, **kwargs):
            calls.append(name)
            return GuardResult(guard_name=name, passed=True, severity=GuardSeverity.LOW, action=GuardAction.PASS)
        return passer

    mapping = {
        "guard_g1_data_integrity": "G1", "guard_g2_feed_health": "G2",
        "guard_g10_orderbook_instability": "G10", "guard_g3_depth_collapse": "G3",
        "guard_g4_spread_explosion": "G4", "guard_g5_oi_anomaly": "G5",
        "guard_g13_oi_divergence": "G13", "guard_g14_oi_stagnation": "G14",
        "guard_g15_oi_percentile_extreme": "G15", "guard_g6_funding_extreme": "G6",
        "guard_g7_news_shock": "G7", "guard_g8_volatility_flash": "G8",
        "guard_g9_btc_regime": "G9", "guard_g11_execution_quality": "G11",
        "guard_g12_self_consistency": "G12",
    }
    for attr, name in mapping.items():
        monkeypatch.setattr(veto, attr, make_passer(name))

    outcome = run_veto_engine(
        snapshot=_minimal_snapshot_no_feed(), news_state=_empty_news(), candidate=None,
        veto_cfg=CFG.veto, symbol_tier="majors",
    )
    assert outcome.veto_state == VetoState.PASS
    assert calls == ["G1", "G2", "G4", "G5", "G6", "G8", "G9"]


def test_veto_engine_degrade_applies_most_restrictive_cap(monkeypatch):
    from app.core.models import GuardResult, GuardSeverity
    from app.risk import veto

    def passer(name):
        def fn(*a, **k):
            return GuardResult(guard_name=name, passed=True, severity=GuardSeverity.LOW, action=GuardAction.PASS)
        return fn

    def degrade_a(*a, **k):
        return GuardResult(guard_name="G8", passed=False, severity=GuardSeverity.HIGH, action=GuardAction.DEGRADE, reason="x", degrade_max_grade="A")

    def degrade_b(*a, **k):
        return GuardResult(guard_name="G9", passed=False, severity=GuardSeverity.MEDIUM, action=GuardAction.DEGRADE, reason="y", degrade_max_grade="B")

    for attr, name in {
        "guard_g1_data_integrity": "G1", "guard_g2_feed_health": "G2",
        "guard_g10_orderbook_instability": "G10", "guard_g3_depth_collapse": "G3",
        "guard_g4_spread_explosion": "G4", "guard_g5_oi_anomaly": "G5",
        "guard_g13_oi_divergence": "G13", "guard_g14_oi_stagnation": "G14",
        "guard_g15_oi_percentile_extreme": "G15", "guard_g6_funding_extreme": "G6",
        "guard_g7_news_shock": "G7", "guard_g11_execution_quality": "G11",
        "guard_g12_self_consistency": "G12",
    }.items():
        monkeypatch.setattr(veto, attr, passer(name))
    monkeypatch.setattr(veto, "guard_g8_volatility_flash", degrade_a)
    monkeypatch.setattr(veto, "guard_g9_btc_regime", degrade_b)

    outcome = run_veto_engine(
        snapshot=_minimal_snapshot_no_feed(), news_state=_empty_news(), candidate=None,
        veto_cfg=CFG.veto, symbol_tier="majors",
    )
    assert outcome.veto_state == VetoState.PASS
    assert outcome.max_grade_cap == "B"
