"""BTC regime detection for guard G9.

Reuses the same EMA trend-regime calculation as S4 (EMA21/EMA55 on 1H,
slope test), computed once for BTCUSDT and shared across every other
symbol's G9 evaluation in a given evaluation tick, per VETO_SPEC.md's
G9 definition.
"""

from __future__ import annotations

from app.core.math import InsufficientDataError, ema_series, wilder_atr
from app.core.models import MarketSnapshot


def compute_btc_trend_direction(btc_snapshot: MarketSnapshot, cfg: dict) -> str | None:
    """Returns "up", "down", or "flat", or None if insufficient data.

    `cfg` is the s4_oi_trend config section (ema_fast_period,
    ema_slow_period, ema_slope_lookback), reused here since G9 defines
    BTC regime identically to S4's own trend-regime test.
    """
    bars_1h = btc_snapshot.klines_for("1h")
    ema_fast_period = cfg["ema_fast_period"]
    ema_slow_period = cfg["ema_slow_period"]
    ema_slope_lookback = cfg["ema_slope_lookback"]

    required = ema_slow_period + ema_slope_lookback + 1
    if len(bars_1h) < required:
        return None

    try:
        atr14_1h = wilder_atr(bars_1h, period=14)
        closes = [b.close for b in bars_1h]
        ema_fast_series = ema_series(closes, ema_fast_period)
        ema_slow_series = ema_series(closes, ema_slow_period)
    except InsufficientDataError:
        return None

    if atr14_1h <= 0:
        return None

    ema_fast_now = ema_fast_series[-1]
    ema_slow_now = ema_slow_series[-1]

    slope_idx = -(ema_slope_lookback + 1)
    if abs(slope_idx) > len(ema_fast_series):
        return None
    ema_fast_prior = ema_fast_series[slope_idx]
    slope = (ema_fast_now - ema_fast_prior) / atr14_1h

    if ema_fast_now > ema_slow_now and slope > 0:
        return "up"
    if ema_fast_now < ema_slow_now and slope < 0:
        return "down"
    return "flat"
