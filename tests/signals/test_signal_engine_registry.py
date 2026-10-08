from __future__ import annotations

import re

import pytest

from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    MarketSnapshot,
    OrderBookState,
    VetoOutcome,
    VetoState,
)
from app.signals.signal_engine import build_final_signal, generate_signal_id
from app.strategies.base import REQUIRED_META_KEYS, StrategyBase, validate_meta_contract
from app.strategies.registry import all_strategies, get_strategy, registered_strategy_ids

EXPIRY = {"A+": 60, "A": 45, "B": 30}


def _snapshot():
    return MarketSnapshot(
        snapshot_version="v1", symbol="BTCUSDT", as_of_ts_ms=1_700_000_000_000,
        klines={},
        orderbook=OrderBookState(
            symbol="BTCUSDT", best_bid=99.995, best_ask=100.005,
            bid_depth_5lvl_usd=500_000, ask_depth_5lvl_usd=500_000,
            event_ts_ms=1, received_ts_ms=1,
        ),
        taker_flow=None, derivatives=None, feed_health={},
        price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=2.0, fee_taker_bps=5.0,
    )


def _candidate(direction=Direction.LONG, **overrides):
    if direction == Direction.LONG:
        base = dict(entry_low=100.03, entry_high=100.47, stop_loss=90.01, tp1=110.0, tp2=120.0, tp3=130.0, tp4=150.0)
    else:
        base = dict(entry_low=99.53, entry_high=99.97, stop_loss=110.02, tp1=90.0, tp2=80.0, tp3=70.0, tp4=50.0)
    base.update(overrides)
    return CandidateSignal(
        symbol="BTCUSDT", direction=direction, strategy_source="S1", confidence=0.7,
        channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
        why_lines=("reason",), meta={"atr14": 1.0, "snapshot_version": "v1", "event_ts_ms": 1},
        event_ts_ms=1, **base,
    )


def _pass_outcome():
    return VetoOutcome(veto_state=VetoState.PASS, veto_reason=None, max_grade_cap=None, guard_results=())


def _block_outcome():
    return VetoOutcome(veto_state=VetoState.BLOCK, veto_reason="G5: OI data is stale", max_grade_cap=None, guard_results=())


def test_generate_signal_id_format():
    sid = generate_signal_id(1_700_000_000_000)
    assert re.fullmatch(r"CSB-\d{8}-[0-9A-F]{6}", sid)


def test_generate_signal_id_unique():
    assert generate_signal_id(1) != generate_signal_id(1)


def test_build_final_signal_long_tick_aligned():
    sig = build_final_signal(
        candidate=_candidate(), snapshot=_snapshot(), grade="A", confidence_weighted=0.7,
        veto_outcome=_pass_outcome(), size_units_advisory=1.0, notional_usd_advisory=100.0,
        expiry_per_grade=EXPIRY, created_ts_ms=1_700_000_000_000,
    )
    for price in (sig.entry_low, sig.entry_high, sig.stop_loss, sig.tp1, sig.tp2, sig.tp3, sig.tp4):
        assert abs(round(price / 0.1) * 0.1 - price) < 1e-6
    assert sig.veto_state == "PASS"
    assert sig.expiry_ts_ms == 1_700_000_000_000 + 45 * 60_000


def test_build_final_signal_short_valid():
    sig = build_final_signal(
        candidate=_candidate(Direction.SHORT), snapshot=_snapshot(), grade="B", confidence_weighted=0.6,
        veto_outcome=_pass_outcome(), size_units_advisory=1.0, notional_usd_advisory=100.0,
        expiry_per_grade=EXPIRY, created_ts_ms=1_700_000_000_000,
    )
    assert sig.direction == "SHORT"
    assert sig.tp4 < sig.tp3 < sig.tp2 < sig.tp1 < sig.entry_low <= sig.entry_high < sig.stop_loss


def test_build_final_signal_blocked_still_constructs_with_reason():
    sig = build_final_signal(
        candidate=_candidate(), snapshot=_snapshot(), grade="B", confidence_weighted=0.6,
        veto_outcome=_block_outcome(), size_units_advisory=1.0, notional_usd_advisory=100.0,
        expiry_per_grade=EXPIRY, created_ts_ms=1_700_000_000_000,
    )
    assert sig.veto_state == "BLOCK"
    assert "G5" in sig.veto_reason


def test_build_final_signal_meta_includes_audit_fields():
    sig = build_final_signal(
        candidate=_candidate(), snapshot=_snapshot(), grade="A", confidence_weighted=0.7,
        veto_outcome=_pass_outcome(), size_units_advisory=1.0, notional_usd_advisory=100.0,
        expiry_per_grade=EXPIRY, created_ts_ms=1_700_000_000_000,
    )
    for key in ("atr14", "snapshot_version", "channels", "cost_entry_maker_fee_per_unit",
                "cost_tp_maker_fee_per_unit", "cost_sl_taker_fee_per_unit", "cost_sl_slippage_per_unit",
                "round_trip_at_tp_per_unit", "round_trip_at_sl_per_unit", "guard_results"):
        assert key in sig.meta


def test_registry_contains_all_five_strategies():
    assert registered_strategy_ids() == ["S1", "S2", "S3", "S4", "S5"]
    assert len(all_strategies()) == 5


def test_registry_get_strategy_and_unknown():
    assert get_strategy("S1").strategy_id == "S1"
    with pytest.raises(KeyError):
        get_strategy("S9")


def test_strategy_base_is_abstract():
    with pytest.raises(TypeError):
        StrategyBase()  # type: ignore[abstract]


def test_validate_meta_contract_reports_missing_keys():
    c = _candidate()
    bad = CandidateSignal(
        symbol=c.symbol, direction=c.direction, strategy_source="S1", confidence=0.5,
        channels=c.channels, entry_low=c.entry_low, entry_high=c.entry_high, stop_loss=c.stop_loss,
        tp1=c.tp1, tp2=c.tp2, tp3=c.tp3, tp4=c.tp4, why_lines=c.why_lines, meta={"atr14": 1.0}, event_ts_ms=1,
    )
    missing = validate_meta_contract(bad)
    assert set(missing) == set(REQUIRED_META_KEYS) - {"atr14"}
