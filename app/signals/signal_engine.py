"""Signal engine.

Takes a CandidateSignal (from a strategy), a grade (from
risk/consensus.py), a VetoOutcome (from risk/veto_engine.py), and
sizing inputs (from risk/risk_engine.py) and produces a final,
validated Signal ready for persistence and (if PASS) Telegram
delivery.

This is the single place where signal_id generation, expiry
computation, and R:R finalization happen — every other module upstream
deals in CandidateSignal, not Signal.
"""

from __future__ import annotations

import uuid

from app.core.math import round_to_tick
from app.core.models import CandidateSignal, Direction, MarketSnapshot, VetoOutcome
from app.core.time_utils import minutes_to_ms, utc_date_str
from app.signals.expiry import compute_expiry_ts_ms
from app.signals.models import Signal, build_signal
from app.signals.tpsl import compute_rr_at_tp, estimate_cost
from app.signals.structure_tp_sl import StructurePolicyConfig, apply_structure_aware_policy


def generate_signal_id(created_ts_ms: int) -> str:
    date_str = utc_date_str(created_ts_ms)
    suffix = uuid.uuid4().hex[:6].upper()
    return f"CSB-{date_str}-{suffix}"


def build_final_signal(
    *,
    candidate: CandidateSignal,
    snapshot: MarketSnapshot,
    grade: str,
    confidence_weighted: float,
    veto_outcome: VetoOutcome,
    size_units_advisory: float,
    notional_usd_advisory: float,
    expiry_per_grade: dict[str, float],
    created_ts_ms: int,
    sizing_multiplier: float = 1.0,
    regime: str = "UNKNOWN",
    htf_confluence: bool = False,
    entry_expiry_minutes: float = 30,
    management_horizon_minutes: float | None = None,
    time_stop_minutes: int = 60,
    partial_exit_fractions: tuple[float, float, float, float] = (0.40, 0.30, 0.20, 0.10),
    structure_aware_tpsl_mode: str = "off",
    structure_policy_config: StructurePolicyConfig | None = None,
) -> Signal:
    """Construct the final Signal from all upstream computation.

    If `veto_outcome.veto_state` is BLOCK, the Signal is still fully
    constructed and validated (for audit/shadow-mode persistence) but
    callers (main.py / telegram layer) MUST check `veto_state` before
    ever delivering it — this function does not itself prevent
    delivery of a blocked signal, that responsibility belongs to the
    Telegram layer's explicit precondition assertion.
    """
    # Strategies compute entry/SL/TP levels from raw ATR-based math and
    # do not themselves know the symbol's exchange price_tick (that is
    # deliberately a snapshot/exchange-filter concern, not a strategy
    # concern — see STRATEGIES_SPEC.md). This is the seam where domain
    # math meets exchange constraints, so every price is tick-aligned
    # here, once, before any downstream consumer (cost model, R:R,
    # Signal construction) sees it. Rounding direction is deliberately
    # conservative per side: stops round away from entry (wider,
    # safer), targets round toward entry (more conservative reward)
    # — but since round_to_tick rounds to nearest, and price_tick is
    # always far smaller than any of these distances, nearest-rounding
    # introduces negligible bias while guaranteeing exchange-valid
    # prices; the Signal validator would otherwise reject perfectly
    # sound strategy output purely for tick-grid noise.
    tick = snapshot.price_tick
    if structure_aware_tpsl_mode not in {"off", "shadow", "enabled"}:
        raise ValueError("structure_aware_tpsl_mode must be off, shadow, or enabled")
    shadow_policy = None
    if structure_aware_tpsl_mode != "off":
        shadow_policy = apply_structure_aware_policy(
            candidate=candidate, snapshot=snapshot,
            config=structure_policy_config or StructurePolicyConfig(),
        )
        if structure_aware_tpsl_mode == "enabled" and not shadow_policy.accepted:
            raise ValueError(f"structure-aware TP/SL rejected signal: {shadow_policy.reason}")
    policy = shadow_policy if structure_aware_tpsl_mode == "enabled" and shadow_policy and shadow_policy.accepted else None
    entry_low = round_to_tick(policy.entry_low if policy else candidate.entry_low, tick)
    entry_high = round_to_tick(policy.entry_high if policy else candidate.entry_high, tick)
    stop_loss = round_to_tick(policy.stop_loss if policy else candidate.stop_loss, tick)
    tp1 = round_to_tick(policy.tp1 if policy else candidate.tp1, tick)
    tp2 = round_to_tick(policy.tp2 if policy else candidate.tp2, tick)
    tp3 = round_to_tick(policy.tp3 if policy else candidate.tp3, tick)
    tp4 = round_to_tick(policy.tp4 if policy else candidate.tp4, tick)

    # entry_price is the entry-zone MIDPOINT, matching the R convention
    # every strategy uses (see app/strategies/base.py docstring): R and
    # every TP_k are defined from this same midpoint, so this is the
    # only point at which R:R can be graded consistently with how the
    # strategy constructed its targets.
    entry_price = (entry_low + entry_high) / 2
    cost = estimate_cost(
        entry_price=entry_price,
        stop_price=stop_loss,
        snapshot=snapshot,
        notional_usd=notional_usd_advisory,
    )

    # R:R at TP2 uses round_trip_at_tp = 2 x maker (limit entry + limit
    # TP). See app/backtest/costs.py for the signal-only justification.
    rr_tp2 = compute_rr_at_tp(
        direction=candidate.direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=tp2,
        cost=cost,
    )

    signal_id = generate_signal_id(created_ts_ms)
    entry_expiry_ts_ms = created_ts_ms + minutes_to_ms(entry_expiry_minutes)
    if management_horizon_minutes is None:
        management_expiry_ts_ms = compute_expiry_ts_ms(
            created_ts_ms=created_ts_ms, grade=grade, expiry_per_grade=expiry_per_grade
        )
        management_minutes = expiry_per_grade[grade]
    else:
        if management_horizon_minutes <= entry_expiry_minutes:
            raise ValueError("management_horizon_minutes must exceed entry_expiry_minutes")
        management_expiry_ts_ms = created_ts_ms + minutes_to_ms(management_horizon_minutes)
        management_minutes = management_horizon_minutes
    effective_time_stop_minutes = min(int(time_stop_minutes), int(management_minutes))

    merged_meta = dict(candidate.meta)
    merged_meta["channels"] = [c.value for c in candidate.channels]
    merged_meta["cost_entry_maker_fee_per_unit"] = cost.entry_maker_fee
    merged_meta["cost_tp_maker_fee_per_unit"] = cost.tp_maker_fee
    merged_meta["cost_sl_taker_fee_per_unit"] = cost.sl_taker_fee
    merged_meta["cost_sl_slippage_per_unit"] = cost.sl_leg_slippage
    merged_meta["round_trip_at_tp_per_unit"] = cost.round_trip_at_tp
    merged_meta["round_trip_at_sl_per_unit"] = cost.round_trip_at_sl
    merged_meta["model"] = "B"
    merged_meta["entry_validity_minutes"] = entry_expiry_minutes
    merged_meta["management_horizon_minutes"] = management_minutes
    merged_meta["time_stop_minutes"] = effective_time_stop_minutes
    merged_meta["partial_exit_fractions"] = list(partial_exit_fractions)
    merged_meta["structure_aware_tpsl_mode"] = structure_aware_tpsl_mode
    if shadow_policy is not None:
        merged_meta["structure_aware_shadow"] = {
            "accepted": shadow_policy.accepted,
            "reason": shadow_policy.reason,
            "source": shadow_policy.source,
            "entry_low": shadow_policy.entry_low,
            "entry_high": shadow_policy.entry_high,
            "stop_loss": shadow_policy.stop_loss,
            "tp1": shadow_policy.tp1,
            "tp2": shadow_policy.tp2,
            "tp3": shadow_policy.tp3,
            "tp4": shadow_policy.tp4,
        }
    merged_meta["guard_results"] = [
        {
            "guard_name": g.guard_name,
            "passed": g.passed,
            "severity": g.severity.value,
            "action": g.action.value,
            "reason": g.reason,
        }
        for g in veto_outcome.guard_results
    ]

    return build_signal(
        signal_id=signal_id,
        created_ts_ms=created_ts_ms,
        symbol=candidate.symbol,
        direction=candidate.direction.value,
        grade=grade,
        confidence=confidence_weighted,
        strategy_source=candidate.strategy_source,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        tp4=tp4,
        rr_tp2=rr_tp2,
        expiry_ts_ms=management_expiry_ts_ms,
        entry_expiry_ts_ms=entry_expiry_ts_ms,
        management_horizon_minutes=int(management_minutes),
        time_stop_minutes=effective_time_stop_minutes,
        partial_exit_fractions=partial_exit_fractions,
        why_lines=list(candidate.why_lines),
        veto_state=veto_outcome.veto_state.value,
        veto_reason=veto_outcome.veto_reason,
        size_units_advisory=size_units_advisory,
        notional_usd_advisory=notional_usd_advisory,
        sizing_multiplier=sizing_multiplier,
        regime=regime,
        htf_confluence=htf_confluence,
        meta=merged_meta,
        price_tick=snapshot.price_tick,
        qty_step=snapshot.qty_step,
    )
