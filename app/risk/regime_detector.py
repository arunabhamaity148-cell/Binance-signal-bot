"""Market-regime detection used by the Phase 1 signal-quality filters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.math import InsufficientDataError, percentile_rank, true_range, wilder_atr_series
from app.core.logging import get_logger
from app.core.models import MarketSnapshot

logger = get_logger(__name__)
_ATR_UNAVAILABLE_WARNED_SYMBOLS: set[str] = set()

class MarketRegime(StrEnum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeMetrics:
    adx14: float | None
    atr_percentile: float | None
    oi_change_1h_pct: float | None
    funding_z: float | None


def classify_regime(*, adx14: float | None, atr_percentile: float | None,
                    oi_change_1h_pct: float | None) -> MarketRegime:
    """Apply the configured Phase 1 classification rules.

    Missing any required metric is fail-closed: no strategy is eligible when
    the regime cannot be determined honestly.
    """
    if adx14 is None or oi_change_1h_pct is None:
        return MarketRegime.UNKNOWN
    if atr_percentile is None:
        return MarketRegime.RANGING
    if atr_percentile > 0.90 or oi_change_1h_pct > 5.0:
        return MarketRegime.HIGH_VOLATILITY
    if adx14 > 25.0 and abs(oi_change_1h_pct) > 1.0:
        return MarketRegime.TRENDING
    return MarketRegime.RANGING


def _adx14(bars) -> float | None:
    period = 14
    if len(bars) < period * 2 + 1:
        return None
    tr_values: list[float] = []
    plus_values: list[float] = []
    minus_values: list[float] = []
    for i in range(1, len(bars)):
        current, previous = bars[i], bars[i - 1]
        tr_values.append(true_range(current, previous.close))
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_values.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_values.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    tr_s = sum(tr_values[:period])
    plus_s = sum(plus_values[:period])
    minus_s = sum(minus_values[:period])
    dx_values: list[float] = []

    def append_dx() -> None:
        plus_di = 100.0 * plus_s / tr_s if tr_s else 0.0
        minus_di = 100.0 * minus_s / tr_s if tr_s else 0.0
        denom = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / denom if denom else 0.0)

    append_dx()
    for i in range(period, len(tr_values)):
        tr_s = tr_s - tr_s / period + tr_values[i]
        plus_s = plus_s - plus_s / period + plus_values[i]
        minus_s = minus_s - minus_s / period + minus_values[i]
        append_dx()
    if len(dx_values) < period:
        return None
    return sum(dx_values[:period]) / period if len(dx_values) == period else (
        (sum(dx_values[:period]) + sum(dx_values[period:])) / len(dx_values)
    )


def _atr_percentile(bars) -> float | None:
    try:
        atr_series = wilder_atr_series(bars, period=14)
    except InsufficientDataError:
        return None
    if len(atr_series) < 201:
        return None
    return percentile_rank(atr_series[-1], atr_series[-201:-1])


def _atr_history_available(bars) -> int:
    try:
        return len(wilder_atr_series(bars, period=14))
    except InsufficientDataError:
        return 0


def metrics_for_snapshot(snapshot: MarketSnapshot, funding_z: float | None) -> RegimeMetrics:
    bars_1h = snapshot.klines_for("1h")
    bars_5m = snapshot.klines_for("5m")
    derivatives = snapshot.derivatives
    oi_history = derivatives.open_interest_history_1h if derivatives else []
    oi_change = None
    if len(oi_history) >= 2 and oi_history[-2].value != 0:
        oi_change = (oi_history[-1].value - oi_history[-2].value) / oi_history[-2].value * 100.0
    atr_history_available = _atr_history_available(bars_5m)
    atr_percentile = _atr_percentile(bars_5m)
    symbol = getattr(snapshot, "symbol", "UNKNOWN")
    logger.info(
        "regime_atr_pct_debug | symbol=%s | klines_available=%s | atr_history_available=%s | "
        "required_for_percentile=201 | computed_atr_pct=%s",
        symbol, len(bars_5m), atr_history_available, atr_percentile,
        extra={"context": {"symbol": symbol, "klines_available": len(bars_5m),
                             "atr_history_available": atr_history_available,
                             "required_for_percentile": 201,
                             "computed_atr_pct": atr_percentile}},
    )
    if atr_percentile is None and symbol not in _ATR_UNAVAILABLE_WARNED_SYMBOLS:
        _ATR_UNAVAILABLE_WARNED_SYMBOLS.add(symbol)
        logger.warning(
            "regime_atr_pct_unavailable | using RANGING default until 200 bars accumulate",
            extra={"context": {"symbol": symbol, "klines_available": len(bars_5m),
                                 "atr_history_available": atr_history_available}},
        )
    return RegimeMetrics(
        adx14=_adx14(bars_1h),
        atr_percentile=atr_percentile,
        oi_change_1h_pct=oi_change,
        funding_z=funding_z,
    )


def detect_regime(snapshot: MarketSnapshot, funding_z: float | None = None) -> tuple[MarketRegime, RegimeMetrics]:
    metrics = metrics_for_snapshot(snapshot, funding_z)
    return classify_regime(
        adx14=metrics.adx14,
        atr_percentile=metrics.atr_percentile,
        oi_change_1h_pct=metrics.oi_change_1h_pct,
    ), metrics
