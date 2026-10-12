from __future__ import annotations

import pytest

from app.core.math import OHLC
from app.core.models import CandidateSignal, ChannelName, Direction, MarketSnapshot, OrderBookState, SymbolKlines, VetoOutcome, VetoState
from app.signals.signal_engine import build_final_signal
from app.signals.structure_tp_sl import StructurePolicyConfig, apply_structure_aware_policy


def _snapshot(with_liquidity: bool = True):
    bars = []
    if with_liquidity:
        for i, high in enumerate((101.0, 101.5, 102.0, 102.4), start=1):
            bars.append(OHLC(open=100.0, high=high, low=99.5, close=100.0, volume=1.0, close_time_ms=i))
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=10,
        klines={"5m": SymbolKlines(symbol="BTCUSDT", timeframe="5m", bars=bars)},
        orderbook=OrderBookState(symbol="BTCUSDT", best_bid=99.9, best_ask=100.1,
                                 bid_depth_5lvl_usd=1_000_000, ask_depth_5lvl_usd=1_000_000,
                                 event_ts_ms=10, received_ts_ms=10),
        taker_flow=None, derivatives=None, feed_health={}, price_tick=0.1,
        qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def _candidate():
    return CandidateSignal(
        symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.8,
        channels=(ChannelName.LIQUIDITY,), entry_low=100.0, entry_high=100.0,
        stop_loss=99.0, tp1=101.0, tp2=102.0, tp3=103.0, tp4=105.0,
        why_lines=("fixture",), event_ts_ms=10,
        meta={"atr14": 1.0, "sweep_extreme": 99.7, "snapshot_version": "v1"},
    )


def test_structure_policy_uses_sweep_extreme_and_observed_liquidity():
    result = apply_structure_aware_policy(
        candidate=_candidate(), snapshot=_snapshot(),
        config=StructurePolicyConfig(buffer_atr=0.2, min_rr_tp2=1.8, max_target_distance_r=40.0),
    )
    assert result.accepted is True
    assert result.stop_loss == pytest.approx(99.5)
    assert (result.tp1, result.tp2, result.tp3, result.tp4) == (101.0, 101.5, 102.0, 102.4)


def test_shadow_mode_keeps_legacy_payload_and_records_proposal():
    candidate = _candidate()
    legacy = build_final_signal(
        candidate=candidate, snapshot=_snapshot(False), grade="A", confidence_weighted=0.8,
        veto_outcome=VetoOutcome(VetoState.PASS, None, None, ()), size_units_advisory=1.0,
        notional_usd_advisory=100.0, expiry_per_grade={"A+": 60, "A": 45, "B": 30},
        created_ts_ms=10,
    )
    shadow = build_final_signal(
        candidate=candidate, snapshot=_snapshot(), grade="A", confidence_weighted=0.8,
        veto_outcome=VetoOutcome(VetoState.PASS, None, None, ()), size_units_advisory=1.0,
        notional_usd_advisory=100.0, expiry_per_grade={"A+": 60, "A": 45, "B": 30},
        created_ts_ms=10, structure_aware_tpsl_mode="shadow",
    )
    assert (shadow.stop_loss, shadow.tp1, shadow.tp2, shadow.tp3, shadow.tp4) == (
        legacy.stop_loss, legacy.tp1, legacy.tp2, legacy.tp3, legacy.tp4
    )
    assert shadow.meta["structure_aware_shadow"]["accepted"] is True


def test_enabled_mode_rejects_when_no_observed_liquidity_can_justify_targets():
    with pytest.raises(ValueError, match="structure-aware TP/SL rejected"):
        build_final_signal(
            candidate=_candidate(), snapshot=_snapshot(False), grade="A", confidence_weighted=0.8,
            veto_outcome=VetoOutcome(VetoState.PASS, None, None, ()), size_units_advisory=1.0,
            notional_usd_advisory=100.0, expiry_per_grade={"A+": 60, "A": 45, "B": 30},
            created_ts_ms=10, structure_aware_tpsl_mode="enabled",
        )
