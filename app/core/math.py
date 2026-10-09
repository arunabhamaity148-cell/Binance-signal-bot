"""Deterministic numeric primitives shared by every strategy and guard.

All functions here are pure: same input, same output, no I/O, no
reliance on wall-clock time. They operate on plain sequences of floats
(closed-bar values only — callers are responsible for never passing an
unclosed/forming bar into these functions).

Every function raises ``InsufficientDataError`` rather than silently
returning a degraded or default value when there is not enough history
to compute a meaningful result. This mirrors the fail-closed principle:
callers (strategies, guards) catch this and translate it into a
NO TRADE / BLOCK outcome, but the math layer itself never guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pstdev


class InsufficientDataError(Exception):
    """Not enough history was supplied to compute the requested value."""


@dataclass(frozen=True)
class OHLC:
    """One closed bar. Immutable."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                "OHLC violates low <= open,close <= high: "
                f"o={self.open} h={self.high} l={self.low} c={self.close}"
            )
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) > high ({self.high})")
        if self.volume < 0:
            raise ValueError(f"negative volume: {self.volume}")


def true_range(current: OHLC, prev_close: float) -> float:
    """True range for a single bar given the prior bar's close."""
    return max(
        current.high - current.low,
        abs(current.high - prev_close),
        abs(current.low - prev_close),
    )


def wilder_atr(bars: list[OHLC], period: int = 14) -> float:
    """Wilder's ATR over the given period: the CURRENT (most recent)
    ATR value, using the full recursive Wilder smoothing formula over
    however much history `bars` provides.

    ``bars`` must be ordered oldest-first and contain at least
    ``period + 1`` bars (the extra bar supplies the seed previous
    close for the first true-range calculation).

    CORRECTNESS NOTE (found during Batch 3 review): Wilder's ATR is a
    recursive, infinite-memory smoothing formula — the correct "current"
    ATR depends on the ENTIRE history supplied, not just the most recent
    `period + 1` bars. An earlier version of this function re-seeded the
    smoothing from only the last `period + 1` bars on every call,
    discarding all prior smoothing memory; that silently produced a
    DIFFERENT (and less correct) number than `wilder_atr_series(bars,
    period)[-1]` whenever `bars` was longer than `period + 1` — exactly
    the situation guard G12's self-consistency recomputation exposed
    when it disagreed with the ATR value S2 (the one strategy computing
    via `wilder_atr_series`) had recorded in a candidate's meta. Every
    other caller of this function (S1, S3, S4, S5, G8, and
    btc_regime.py) was calling the re-seeded version without ever
    surfacing the discrepancy, because they never cross-checked against
    `wilder_atr_series`. This function is now a thin, deliberately
    trivial wrapper around `wilder_atr_series` so the two can never
    disagree again — there is exactly one Wilder-ATR implementation.
    """
    return wilder_atr_series(bars, period=period)[-1]


def wilder_atr_series(bars: list[OHLC], period: int = 14) -> list[float]:
    """Full ATR series (one value per bar from index `period` onward).

    Returned list has length ``len(bars) - period``. Element 0
    corresponds to ``bars[period]``.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(bars) < period + 1:
        raise InsufficientDataError(
            f"wilder_atr_series requires at least {period + 1} bars, got {len(bars)}"
        )

    trs: list[float] = [
        true_range(bars[idx], bars[idx - 1].close) for idx in range(1, len(bars))
    ]
    series: list[float] = []
    atr = fmean(trs[:period])
    series.append(atr)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        series.append(atr)
    return series


def ema(values: list[float], period: int) -> float:
    """Exponential moving average, seeded with a simple average of the
    first `period` values (standard convention)."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise InsufficientDataError(
            f"ema requires at least {period} values, got {len(values)}"
        )
    k = 2.0 / (period + 1)
    avg = fmean(values[:period])
    for v in values[period:]:
        avg = v * k + avg * (1 - k)
    return avg


def ema_series(values: list[float], period: int) -> list[float]:
    """Full EMA series. Returned list has length ``len(values) - period + 1``.
    Element 0 corresponds to ``values[period - 1]``."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise InsufficientDataError(
            f"ema_series requires at least {period} values, got {len(values)}"
        )
    k = 2.0 / (period + 1)
    avg = fmean(values[:period])
    out = [avg]
    for v in values[period:]:
        avg = v * k + avg * (1 - k)
        out.append(avg)
    return out


def rolling_median(values: list[float]) -> float:
    if not values:
        raise InsufficientDataError("rolling_median requires at least 1 value")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def percentile_rank(current: float, history: list[float]) -> float:
    """Fraction of `history` less than or equal to `current`, in [0, 1].

    ``history`` should NOT include `current` itself unless the caller
    intends `current` to be compared against itself as well (i.e. pass
    the trailing window excluding the current bar for a
    look-ahead-free percentile).

    The inclusive convention gives tied minimum values a positive rank and
    still maps the maximum value in the comparison window to exactly 1.0.
    """
    if not history:
        raise InsufficientDataError("percentile_rank requires non-empty history")
    n = len(history)
    count_at_or_below = sum(1 for h in history if h <= current)
    return count_at_or_below / n


def rank_index(current: float, history_including_current: list[float]) -> int:
    """1-based rank of `current` within `history_including_current`,
    ordered by descending value (rank 1 = highest). Used by the
    effective-vote consensus weighting in risk/consensus.py."""
    if not history_including_current:
        raise InsufficientDataError("rank_index requires non-empty history")
    sorted_desc = sorted(history_including_current, reverse=True)
    # First matching position (stable, ties resolved by first occurrence)
    return sorted_desc.index(current) + 1


def zscore(current: float, window: list[float]) -> float:
    """Z-score of `current` against `window` (population stddev).

    ``window`` should be the trailing sample used to compute mean/std;
    by convention callers include `current` as the most recent element
    of ``window`` when that matches the intended definition (e.g. S3's
    funding_z uses the trailing W samples including the current one).
    """
    if len(window) < 2:
        raise InsufficientDataError("zscore requires at least 2 values in window")
    mu = fmean(window)
    sigma = pstdev(window)
    if sigma == 0:
        raise InsufficientDataError("zscore undefined: zero standard deviation")
    return (current - mu) / sigma


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        raise InsufficientDataError("pct_change undefined: previous value is zero")
    return (current - previous) / previous


def round_down_to_step(value: float, step: float) -> float:
    """Round `value` down to the nearest multiple of `step`.

    Used for quantity sizing (qty_step) and, with a different step,
    price tick alignment when rounding conservatively.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    steps = int(value / step + 1e-9)  # epsilon guards float repr issues
    return round(steps * step, 12)


def round_to_tick(value: float, tick: float) -> float:
    """Round `value` to the nearest multiple of `tick` (nearest, not
    down) — used for aligning computed prices (entries, stops, TPs) to
    the exchange's price_tick filter."""
    if tick <= 0:
        raise ValueError("tick must be positive")
    steps = round(value / tick)
    return round(steps * tick, 12)


def is_tick_aligned(value: float, tick: float, *, rel_tol: float = 1e-6) -> bool:
    if tick <= 0:
        raise ValueError("tick must be positive")
    nearest = round_to_tick(value, tick)
    return abs(nearest - value) <= max(rel_tol * tick, 1e-9)


def swing_highs_lows(bars: list[OHLC], lookback: int) -> tuple[list[int], list[int]]:
    """Identify swing-high and swing-low bar indices using a fixed
    symmetric lookback: bar i is a swing high if its high is the
    maximum within [i-lookback, i+lookback], and symmetrically for
    swing lows. This is the single, versioned swing-detection
    algorithm used everywhere "swing" is referenced (S1, S4) — see
    KNOWN_UNCERTAINTIES.md item 7 for why this specific choice was
    made explicit rather than left ambiguous.

    Returns (swing_high_indices, swing_low_indices), both ascending,
    only for indices where a full symmetric window exists.
    """
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    n = len(bars)
    highs: list[int] = []
    lows: list[int] = []
    for i in range(lookback, n - lookback):
        window = bars[i - lookback : i + lookback + 1]
        if bars[i].high == max(b.high for b in window):
            highs.append(i)
        if bars[i].low == min(b.low for b in window):
            lows.append(i)
    return highs, lows


def has_hh_hl_sequence(bars: list[OHLC], lookback: int, swing_count: int) -> bool:
    """True if the last `swing_count` swing highs are each higher than
    the previous, AND the last `swing_count` swing lows are each higher
    than the previous (uptrend structure, used by S4)."""
    highs_idx, lows_idx = swing_highs_lows(bars, lookback)
    if len(highs_idx) < swing_count or len(lows_idx) < swing_count:
        return False
    recent_highs = [bars[i].high for i in highs_idx[-swing_count:]]
    recent_lows = [bars[i].low for i in lows_idx[-swing_count:]]
    highs_rising = all(recent_highs[k] > recent_highs[k - 1] for k in range(1, len(recent_highs)))
    lows_rising = all(recent_lows[k] > recent_lows[k - 1] for k in range(1, len(recent_lows)))
    return highs_rising and lows_rising


def has_lh_ll_sequence(bars: list[OHLC], lookback: int, swing_count: int) -> bool:
    """Mirror of has_hh_hl_sequence for downtrend structure."""
    highs_idx, lows_idx = swing_highs_lows(bars, lookback)
    if len(highs_idx) < swing_count or len(lows_idx) < swing_count:
        return False
    recent_highs = [bars[i].high for i in highs_idx[-swing_count:]]
    recent_lows = [bars[i].low for i in lows_idx[-swing_count:]]
    highs_falling = all(recent_highs[k] < recent_highs[k - 1] for k in range(1, len(recent_highs)))
    lows_falling = all(recent_lows[k] < recent_lows[k - 1] for k in range(1, len(recent_lows)))
    return highs_falling and lows_falling
