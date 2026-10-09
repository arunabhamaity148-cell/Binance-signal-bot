"""G16 - Multi-timeframe confluence.

A candidate must agree with both the 1H EMA trend and the 4H swing
structure. Missing higher-timeframe data fails closed for candidates.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.monitoring.diagnostics import is_verbose
from app.core.math import InsufficientDataError, ema_series, has_hh_hl_sequence, has_lh_ll_sequence
from app.core.models import CandidateSignal, Direction, GuardAction, GuardResult, GuardSeverity, MarketSnapshot, NewsState

logger = get_logger(__name__)


def guard_g16_multi_tf_confluence(
    snapshot: MarketSnapshot,
    news: NewsState,
    candidate: CandidateSignal | None,
    cfg: dict,
) -> GuardResult:
    if candidate is None:
        return GuardResult("G16", True, GuardSeverity.HIGH, GuardAction.PASS)

    bars_1h = snapshot.klines_for("1h")
    bars_4h = snapshot.klines_for("4h")
    fast_period = int(cfg["htf_1h_ema_fast"])
    slow_period = int(cfg["htf_1h_ema_slow"])
    structure_bars = int(cfg["htf_4h_structure_bars"])
    if len(bars_1h) < slow_period or len(bars_4h) < structure_bars:
        return _block(snapshot, candidate, None, None, "missing higher-timeframe data")

    try:
        closes = [bar.close for bar in bars_1h]
        fast = ema_series(closes, fast_period)[-1]
        slow = ema_series(closes, slow_period)[-1]
        ema50 = ema_series(closes, 50)[-1]
    except InsufficientDataError:
        return _block(snapshot, candidate, None, None, "insufficient 1H EMA history")

    last_price = closes[-1]
    long_trend = fast > slow or last_price > ema50
    short_trend = fast < slow or last_price < ema50
    trend = "up" if long_trend else "down" if short_trend else "flat"
    structure_slice = bars_4h[-structure_bars:]
    structure = "hh_hl" if has_hh_hl_sequence(structure_slice, 3, 2) else (
        "lh_ll" if has_lh_ll_sequence(structure_slice, 3, 2) else "mixed"
    )
    aligned = (
        candidate.direction == Direction.LONG and long_trend and structure == "hh_hl"
    ) or (
        candidate.direction == Direction.SHORT and short_trend and structure == "lh_ll"
    )
    context = (
        f"G16_check | symbol={snapshot.symbol} | direction={candidate.direction.value} | "
        f"1h_ema_fast={fast:.6f} | 1h_ema_slow={slow:.6f} | 1h_trend={trend} | "
        f"4h_structure={structure} | passed={aligned}"
    )
    if is_verbose():
        logger.info(context)
    if aligned:
        return GuardResult("G16", True, GuardSeverity.HIGH, GuardAction.PASS)
    return GuardResult("G16", False, GuardSeverity.HIGH, GuardAction.BLOCK, context)


def _block(snapshot, candidate, fast, slow, reason: str) -> GuardResult:
    context = (
        f"G16_check | symbol={snapshot.symbol} | direction={candidate.direction.value} | "
        f"1h_ema_fast={fast} | 1h_ema_slow={slow} | 1h_trend=unknown | "
        f"4h_structure=unknown | passed=False | reason={reason}"
    )
    if is_verbose():
        logger.info(context)
    return GuardResult("G16", False, GuardSeverity.HIGH, GuardAction.BLOCK, reason)
