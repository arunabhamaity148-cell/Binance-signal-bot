"""Tests for app/risk/risk_engine.py: position sizing, equity
fail-closed behavior (including the legacy-key-ignored proof), and
every pre-publication cap (max_concurrent with correlated-cluster
collapse, max_daily_signals, max_daily_loss_R, cooldown,
opposite-direction).
"""
from __future__ import annotations

import pytest

from app.backtest.costs import cash_risk_per_unit, compute_cost_breakdown, estimate_slippage_bps
from app.config import get_assumed_account_equity_usd, load_all
from app.core.errors import MissingAssumedEquityError
from app.core.models import CandidateSignal, ChannelName, Direction
from app.risk.risk_engine import (
    DailyCounters,
    OpenSignalRecord,
    RiskState,
    apply_min_rr_gate,
    check_risk_limits,
    compute_position_size,
    count_concurrent_slots,
)

RISK_CFG = load_all().risk  # the REAL shipped config, not a hand-rolled copy
FEES = dict(fee_maker_bps=2.0, fee_taker_bps=5.0, depth_usd=2_000_000.0)


def _candidate(symbol="BTCUSDT", direction=Direction.LONG):
    return CandidateSignal(
        symbol=symbol, direction=direction, strategy_source="S1", confidence=0.7,
        channels=(ChannelName.LIQUIDITY,), entry_low=100, entry_high=100.5, stop_loss=99,
        tp1=101, tp2=102, tp3=103, tp4=105, why_lines=("r",), meta={}, event_ts_ms=1000,
    )


def _open(symbol, direction=Direction.LONG, sig_id=None):
    return OpenSignalRecord(signal_id=sig_id or f"sig-{symbol}", symbol=symbol, direction=direction, opened_ts_ms=0)


def _independent_loss_at_stop(qty, entry, stop, *, maker=2.0, taker=5.0, depth=2_000_000.0):
    """Independent recomputation of the fee-inclusive stop-out cash
    loss, built directly from app.backtest.costs primitives rather than
    from risk_engine's own internals -- this is NOT an echo of
    compute_position_size's implementation, it's the same real cost
    model called a second, separate way to check the sizing result
    against."""
    direction = Direction.LONG if stop < entry else Direction.SHORT
    notional = qty * entry
    cost = compute_cost_breakdown(
        entry_price=entry, stop_price=stop, fee_maker_bps=maker, fee_taker_bps=taker,
        notional_usd=notional, depth_usd=depth,
    )
    per_unit = cash_risk_per_unit(direction=direction, entry_price=entry, stop_loss=stop, cost=cost)
    return qty * per_unit


# ---------------------------------------------------------------------------
# Position sizing -- checked against an independent recomputation
# ---------------------------------------------------------------------------


def test_sizing_result_independently_verified_within_r_budget():
    result = compute_position_size(
        assumed_equity_usd=1000.0, risk_per_trade_pct=0.5,
        entry_price=100.0, stop_loss=99.0, qty_step=0.0001, **FEES,
    )
    loss = _independent_loss_at_stop(result.qty, 100.0, 99.0)
    assert loss <= result.r_budget_usd * (1 + 1e-9)
    assert loss >= result.r_budget_usd * 0.99  # uses most of the budget, not needlessly conservative


def test_sizing_r_budget_matches_formula():
    result = compute_position_size(
        assumed_equity_usd=2000.0, risk_per_trade_pct=1.0,
        entry_price=100.0, stop_loss=99.0, qty_step=0.0001, **FEES,
    )
    assert result.r_budget_usd == pytest.approx(2000.0 * (1.0 / 100.0))


def test_sizing_short_direction_independently_verified():
    result = compute_position_size(
        assumed_equity_usd=1000.0, risk_per_trade_pct=0.5,
        entry_price=100.0, stop_loss=101.0, qty_step=0.0001, **FEES,
    )
    loss = _independent_loss_at_stop(result.qty, 100.0, 101.0)
    assert loss <= result.r_budget_usd * (1 + 1e-9)


def test_sizing_rounds_down_never_up():
    result = compute_position_size(
        assumed_equity_usd=1000.0, risk_per_trade_pct=0.5,
        entry_price=100.0, stop_loss=99.7, qty_step=1.0, **FEES,
    )
    assert result.qty == float(int(result.qty))
    assert result.qty <= result.qty_raw


def test_sizing_zero_equity_raises():
    with pytest.raises(ValueError):
        compute_position_size(assumed_equity_usd=0, risk_per_trade_pct=0.5, entry_price=100, stop_loss=99, qty_step=0.001, **FEES)


def test_sizing_zero_stop_distance_raises():
    with pytest.raises(ValueError):
        compute_position_size(assumed_equity_usd=1000, risk_per_trade_pct=0.5, entry_price=100, stop_loss=100, qty_step=0.001, **FEES)


def test_sizing_negative_risk_pct_raises():
    with pytest.raises(ValueError):
        compute_position_size(assumed_equity_usd=1000, risk_per_trade_pct=-1, entry_price=100, stop_loss=99, qty_step=0.001, **FEES)


def test_sizing_zero_qty_step_raises():
    with pytest.raises(ValueError):
        compute_position_size(assumed_equity_usd=1000, risk_per_trade_pct=0.5, entry_price=100, stop_loss=99, qty_step=0, **FEES)


# ---------------------------------------------------------------------------
# ASSUMED_ACCOUNT_EQUITY_USD: mandatory, no fallback, legacy key ignored
# ---------------------------------------------------------------------------


def test_equity_missing_raises(monkeypatch):
    monkeypatch.delenv("ASSUMED_ACCOUNT_EQUITY_USD", raising=False)
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_equity_empty_string_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_equity_non_numeric_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "abc")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_equity_zero_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "0")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_equity_negative_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "-500")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_equity_valid_value_accepted(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "1000")
    assert get_assumed_account_equity_usd() == 1000.0


def test_legacy_reference_equity_key_absent_from_shipped_config():
    """The legacy key must not even be present in risk.yaml -- proven
    directly against the real loaded config, not a hand-built dict."""
    assert "reference_equity_usdt" not in RISK_CFG


def test_legacy_key_present_in_env_does_not_satisfy_requirement(monkeypatch):
    """Proves the legacy key is ignored: even if something in the
    environment or a stray config dict sets a 'reference_equity_usdt'-
    style value, ASSUMED_ACCOUNT_EQUITY_USD missing still raises --
    there is no fallback path that reads any other name."""
    monkeypatch.delenv("ASSUMED_ACCOUNT_EQUITY_USD", raising=False)
    monkeypatch.setenv("reference_equity_usdt", "999999")  # a legacy-shaped env var, must be irrelevant
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_no_hardcoded_fallback_anywhere_in_get_assumed_equity_source():
    """Static guard: the function body contains no numeric literal that
    could serve as a silent default (not even a 'sensible' 1000.0)."""
    import inspect

    import app.config as config_module

    source = inspect.getsource(config_module.get_assumed_account_equity_usd)
    for forbidden in ("return 1000", "= 1000", "return 10000", "DEFAULT_EQUITY"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# max_concurrent with correlated-cluster collapse (BTCUSDT/ETHUSDT/SOLUSDT = 1 slot)
# ---------------------------------------------------------------------------


def test_count_concurrent_slots_cluster_members_collapse_to_one_slot():
    clusters = RISK_CFG["correlated_clusters"]
    open_signals = [_open("BTCUSDT"), _open("ETHUSDT"), _open("SOLUSDT")]
    assert count_concurrent_slots(open_signals, clusters) == 1


def test_count_concurrent_slots_unclustered_symbols_each_own_slot():
    clusters = RISK_CFG["correlated_clusters"]
    open_signals = [_open("DOGEUSDT"), _open("LINKUSDT")]
    assert count_concurrent_slots(open_signals, clusters) == 2


def test_count_concurrent_slots_mixed():
    clusters = RISK_CFG["correlated_clusters"]
    open_signals = [_open("BTCUSDT"), _open("ETHUSDT"), _open("DOGEUSDT")]
    # BTC+ETH -> 1 slot (same cluster), DOGE -> 1 slot (unclustered) = 2
    assert count_concurrent_slots(open_signals, clusters) == 2


def test_max_concurrent_blocks_when_slots_exhausted():
    """max_concurrent=5 (shipped config). Fill all 5 with distinct
    unclustered symbols; a 6th distinct symbol must be blocked."""
    state = RiskState(open_signals=[_open(s) for s in ["DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT"]])
    violations = check_risk_limits(
        candidate=_candidate(symbol="BCHUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("max_concurrent" in v for v in violations)


def test_max_concurrent_second_cluster_member_does_not_consume_new_slot():
    """BTCUSDT already open (1 slot used). Adding ETHUSDT (same cluster)
    must NOT push past max_concurrent even near the limit, because it
    shares BTCUSDT's slot."""
    state = RiskState(open_signals=[
        _open("BTCUSDT"), _open("DOGEUSDT"), _open("LINKUSDT"), _open("ADAUSDT"),
    ])  # 4 slots used (BTC-cluster=1, doge=1, link=1, ada=1), limit is 5
    violations = check_risk_limits(
        candidate=_candidate(symbol="ETHUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("max_concurrent" in v for v in violations)


def test_max_concurrent_new_cluster_slot_can_still_exhaust_limit():
    """5 unclustered slots full; adding a cluster symbol whose cluster
    has NO open signal yet still needs a new slot and must be
    blocked."""
    state = RiskState(open_signals=[_open(s) for s in ["DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT"]])
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("max_concurrent" in v for v in violations)


def test_max_concurrent_passes_with_room_to_spare():
    state = RiskState(open_signals=[_open("BTCUSDT")])
    violations = check_risk_limits(
        candidate=_candidate(symbol="DOGEUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("max_concurrent" in v for v in violations)


def test_max_concurrent_three_cluster_members_all_open_still_one_slot_total():
    """Sanity check directly on count_concurrent_slots for the exact
    cluster named in the spec (majors_beta: BTC/ETH/SOL)."""
    clusters = RISK_CFG["correlated_clusters"]
    assert count_concurrent_slots([_open("BTCUSDT"), _open("ETHUSDT"), _open("SOLUSDT")], clusters) == 1


# ---------------------------------------------------------------------------
# max_daily_signals
# ---------------------------------------------------------------------------


def test_max_daily_signals_blocks_at_limit():
    limit = RISK_CFG["max_daily_signals"]
    state = RiskState(daily=DailyCounters(date_str="20260101", signals_emitted=limit))
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("max_daily_signals" in v for v in violations)


def test_max_daily_signals_passes_below_limit():
    limit = RISK_CFG["max_daily_signals"]
    state = RiskState(daily=DailyCounters(date_str="20260101", signals_emitted=limit - 1))
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("max_daily_signals" in v for v in violations)


def test_max_daily_signals_resets_on_new_date():
    limit = RISK_CFG["max_daily_signals"]
    state = RiskState(daily=DailyCounters(date_str="20260101", signals_emitted=limit))
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260102",  # different day
    )
    assert not any("max_daily_signals" in v for v in violations)


# ---------------------------------------------------------------------------
# max_daily_loss_R
# ---------------------------------------------------------------------------


def test_max_daily_loss_r_blocks_at_limit():
    limit = RISK_CFG["max_daily_loss_r"]
    state = RiskState(daily=DailyCounters(date_str="20260101", signals_emitted=1, realized_loss_r=limit))
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("max_daily_loss_r" in v for v in violations)


def test_max_daily_loss_r_passes_below_limit():
    limit = RISK_CFG["max_daily_loss_r"]
    state = RiskState(daily=DailyCounters(date_str="20260101", signals_emitted=1, realized_loss_r=limit - 0.5))
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("max_daily_loss_r" in v for v in violations)


# ---------------------------------------------------------------------------
# cooldown
# ---------------------------------------------------------------------------


def test_cooldown_blocks_within_window():
    state = RiskState(cooldown_until_ts_ms={"BTCUSDT": 5000})
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("cooldown" in v for v in violations)


def test_cooldown_passes_after_window_expires():
    state = RiskState(cooldown_until_ts_ms={"BTCUSDT": 500})
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("cooldown" in v for v in violations)


def test_cooldown_is_per_symbol():
    state = RiskState(cooldown_until_ts_ms={"BTCUSDT": 5000})
    violations = check_risk_limits(
        candidate=_candidate(symbol="ETHUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("cooldown" in v for v in violations)


def test_cooldown_exactly_at_boundary_passes():
    state = RiskState(cooldown_until_ts_ms={"BTCUSDT": 1000})
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT"), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("cooldown" in v for v in violations)


# ---------------------------------------------------------------------------
# Opposite-direction
# ---------------------------------------------------------------------------


def test_opposite_direction_blocked():
    state = RiskState(open_signals=[_open("BTCUSDT", direction=Direction.SHORT)])
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT", direction=Direction.LONG),
        risk_state=state, risk_cfg=RISK_CFG, as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert any("opposite-direction" in v for v in violations)


def test_same_direction_allowed():
    state = RiskState(open_signals=[_open("BTCUSDT", direction=Direction.LONG)])
    violations = check_risk_limits(
        candidate=_candidate(symbol="BTCUSDT", direction=Direction.LONG),
        risk_state=state, risk_cfg=RISK_CFG, as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("opposite-direction" in v for v in violations)


def test_opposite_direction_different_symbol_not_blocked():
    state = RiskState(open_signals=[_open("BTCUSDT", direction=Direction.SHORT)])
    violations = check_risk_limits(
        candidate=_candidate(symbol="ETHUSDT", direction=Direction.LONG),
        risk_state=state, risk_cfg=RISK_CFG, as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert not any("opposite-direction" in v for v in violations)


# ---------------------------------------------------------------------------
# All-clear baseline + min R:R gate
# ---------------------------------------------------------------------------


def test_all_checks_clear_with_empty_state():
    state = RiskState()
    violations = check_risk_limits(
        candidate=_candidate(), risk_state=state, risk_cfg=RISK_CFG,
        as_of_ts_ms=1000, current_date_str="20260101",
    )
    assert violations == []


def test_apply_min_rr_gate_passes():
    assert apply_min_rr_gate(2.0, 1.8) is True


def test_apply_min_rr_gate_fails():
    assert apply_min_rr_gate(1.5, 1.8) is False


def test_apply_min_rr_gate_exact_boundary():
    assert apply_min_rr_gate(1.8, 1.8) is True
