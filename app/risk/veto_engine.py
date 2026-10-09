"""Veto engine.

Runs G1-G15 in the exact order specified in VETO_SPEC.md's "Guard
Execution Order and Short-Circuiting" section:

    G1 -> G2 -> G10 -> G3 -> G4 -> (G5, G13, G14, G15) -> G6 -> G7 ->
    G8 -> G9 -> G11 -> G12

Any HARD BLOCK short-circuits remaining guard evaluation for that
candidate (a performance optimization; each guard remains
independently testable in isolation per app/risk/veto.py).

An uncaught exception inside any guard is caught here and converted
into a BLOCK with reason "guard_exception:<GuardName>" — never a
silent pass (spec section 12).
"""

from __future__ import annotations

from app.core.errors import GuardExecutionError
from app.monitoring.diagnostics import level as diagnostic_level, veto as diagnostic_veto
from app.core.models import (
    CandidateSignal,
    GuardAction,
    GuardResult,
    GuardSeverity,
    MarketSnapshot,
    NewsState,
    VetoOutcome,
    VetoState,
)
from app.risk import veto
from app.risk.veto_g16 import guard_g16_multi_tf_confluence


def _safe_call(guard_name: str, fn, *args, **kwargs) -> GuardResult:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - intentional: any guard exception becomes a BLOCK
        wrapped = GuardExecutionError(guard_name, exc)
        return GuardResult(
            guard_name=guard_name,
            passed=False,
            severity=GuardSeverity.CRITICAL,
            action=GuardAction.BLOCK,
            reason=str(wrapped),
        )


def run_veto_engine(
    *,
    snapshot: MarketSnapshot,
    news_state: NewsState,
    candidate: CandidateSignal | None,
    veto_cfg: dict,
    symbol_tier: str,
    funding_z: float | None = None,
    btc_trend_direction: str | None = None,
) -> VetoOutcome:
    """Run all 15 guards in the specified order against one candidate
    (or against the market/feed state alone if candidate is None, used
    for standalone health checks). Returns a VetoOutcome aggregating
    every guard result, the overall veto_state, and any grade cap from
    DEGRADE actions.
    """
    results: list[GuardResult] = []

    results.append(_safe_call("G1", veto.guard_g1_data_integrity, snapshot, news_state, candidate, veto_cfg["g1_data_integrity"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G2", veto.guard_g2_feed_health, snapshot, news_state, candidate, veto_cfg["g2_feed_health"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G10", veto.guard_g10_orderbook_instability, snapshot, news_state, candidate, veto_cfg["g10_orderbook_instability"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G3", veto.guard_g3_depth_collapse, snapshot, news_state, candidate, veto_cfg["g3_depth_collapse"], symbol_tier=symbol_tier))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G4", veto.guard_g4_spread_explosion, snapshot, news_state, candidate, veto_cfg["g4_spread_explosion"], symbol_tier=symbol_tier))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G5", veto.guard_g5_oi_anomaly, snapshot, news_state, candidate, veto_cfg["g5_oi_anomaly"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G13", veto.guard_g13_oi_divergence, snapshot, news_state, candidate, veto_cfg["g13_oi_divergence"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G14", veto.guard_g14_oi_stagnation, snapshot, news_state, candidate, veto_cfg["g14_oi_stagnation"]))
    # G14 is a DEGRADE guard, never a hard block; continue regardless.

    results.append(_safe_call("G15", veto.guard_g15_oi_percentile_extreme, snapshot, news_state, candidate, veto_cfg["g15_oi_percentile_extreme"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    g16_cfg = veto_cfg.get("g16_multi_tf_confluence", {})
    if g16_cfg.get("enabled", True):
        results.append(_safe_call("G16", guard_g16_multi_tf_confluence, snapshot, news_state, candidate, g16_cfg))
        if _is_hard_block(results[-1]):
            return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G6", veto.guard_g6_funding_extreme, snapshot, news_state, candidate, veto_cfg["g6_funding_extreme"], funding_z=funding_z))
    # DEGRADE only, continue.

    results.append(_safe_call("G7", veto.guard_g7_news_shock, snapshot, news_state, candidate, veto_cfg["g7_news_shock"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G8", veto.guard_g8_volatility_flash, snapshot, news_state, candidate, veto_cfg["g8_volatility_flash"]))
    # DEGRADE by default, continue.

    results.append(_safe_call("G9", veto.guard_g9_btc_regime, snapshot, news_state, candidate, veto_cfg["g9_btc_regime"], btc_trend_direction=btc_trend_direction))
    # DEGRADE only, continue.

    results.append(_safe_call(
        "G11", veto.guard_g11_execution_quality, snapshot, news_state, candidate, veto_cfg["g11_execution_quality"],
        symbol_tier=symbol_tier, prior_results=results,
    ))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    results.append(_safe_call("G12", veto.guard_g12_self_consistency, snapshot, news_state, candidate, veto_cfg["g12_self_consistency"]))
    if _is_hard_block(results[-1]):
        return _finalize(results, symbol=snapshot.symbol)

    return _finalize(results, symbol=snapshot.symbol)


def _is_hard_block(result: GuardResult) -> bool:
    return not result.passed and result.action == GuardAction.BLOCK


def _finalize(results: list[GuardResult], *, symbol: str = "UNKNOWN") -> VetoOutcome:
    if diagnostic_level() != "off":
        for result in results:
            if diagnostic_level() == "verbose":
                diagnostic_veto(symbol, result.guard_name, "pass" if result.passed else result.action.value, result.reason, {"severity": result.severity.value, "degrade_max_grade": result.degrade_max_grade})
    blocking = [r for r in results if not r.passed and r.action == GuardAction.BLOCK]
    if blocking:
        diagnostic_veto(symbol, "ENGINE", "BLOCK", "; ".join(r.guard_name for r in blocking), {"evaluated": len(results)})
        reason = "; ".join(f"{r.guard_name}: {r.reason}" for r in blocking)
        return VetoOutcome(
            veto_state=VetoState.BLOCK,
            veto_reason=reason,
            max_grade_cap=None,
            guard_results=tuple(results),
        )

    degrades = [r for r in results if not r.passed and r.action == GuardAction.DEGRADE and r.degrade_max_grade]
    max_grade_cap: str | None = None
    if degrades:
        # Most restrictive cap wins: "B" is more restrictive than "A".
        grade_order = {"A+": 2, "A": 1, "B": 0}
        max_grade_cap = min((d.degrade_max_grade for d in degrades), key=lambda g: grade_order.get(g, 99))

    diagnostic_veto(symbol, "ENGINE", "PASS", None, {"evaluated": len(results), "degraded": len(degrades)})
    return VetoOutcome(
        veto_state=VetoState.PASS,
        veto_reason=None,
        max_grade_cap=max_grade_cap,
        guard_results=tuple(results),
    )
