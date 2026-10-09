"""Production veto engine for the seven signal-only guards.

Execution order:
    G1 -> G2 -> G4 -> G5 -> G6 -> G8 -> G9

A hard block short-circuits later guards. Guard exceptions fail closed as
explicit blocks; no guard result is silently swallowed.
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


_ACTIVE_GUARDS = ("G1", "G2", "G4", "G5", "G6", "G8", "G9")


def _safe_call(guard_name: str, fn, *args, **kwargs) -> GuardResult:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - guard failures must fail closed
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
    """Evaluate the seven configured production guards for one snapshot."""
    results: list[GuardResult] = []

    def run_guard(name: str, config_key: str, fn, *args, **kwargs) -> bool:
        config = veto_cfg.get(config_key, {})
        if not config.get("enabled", True):
            return False
        results.append(_safe_call(name, fn, *args, **kwargs))
        return _is_hard_block(results[-1])

    if run_guard("G1", "g1_data_integrity", veto.guard_g1_data_integrity, snapshot, news_state, candidate, veto_cfg["g1_data_integrity"]):
        return _finalize(results, symbol=snapshot.symbol)
    if run_guard("G2", "g2_feed_health", veto.guard_g2_feed_health, snapshot, news_state, candidate, veto_cfg["g2_feed_health"]):
        return _finalize(results, symbol=snapshot.symbol)
    if run_guard("G4", "g4_spread_explosion", veto.guard_g4_spread_explosion, snapshot, news_state, candidate, veto_cfg["g4_spread_explosion"], symbol_tier=symbol_tier):
        return _finalize(results, symbol=snapshot.symbol)
    if run_guard("G5", "g5_oi_anomaly", veto.guard_g5_oi_anomaly, snapshot, news_state, candidate, veto_cfg["g5_oi_anomaly"]):
        return _finalize(results, symbol=snapshot.symbol)
    run_guard("G6", "g6_funding_extreme", veto.guard_g6_funding_extreme, snapshot, news_state, candidate, veto_cfg["g6_funding_extreme"], funding_z=funding_z)
    if run_guard("G8", "g8_volatility_flash", veto.guard_g8_volatility_flash, snapshot, news_state, candidate, veto_cfg["g8_volatility_flash"]):
        return _finalize(results, symbol=snapshot.symbol)
    run_guard("G9", "g9_btc_regime", veto.guard_g9_btc_regime, snapshot, news_state, candidate, veto_cfg["g9_btc_regime"], btc_trend_direction=btc_trend_direction)
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
        grade_order = {"A+": 2, "A": 1, "B": 0}
        max_grade_cap = min((d.degrade_max_grade for d in degrades), key=lambda g: grade_order.get(g, 99))

    diagnostic_veto(symbol, "ENGINE", "PASS", None, {"evaluated": len(results), "degraded": len(degrades)})
    return VetoOutcome(
        veto_state=VetoState.PASS,
        veto_reason=None,
        max_grade_cap=max_grade_cap,
        guard_results=tuple(results),
    )
