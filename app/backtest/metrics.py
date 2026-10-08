"""Backtest performance metrics, per spec section 22: trades, win rate,
gross R / net R / avg R, expectancy, profit factor, max drawdown (in
R), consecutive losses, tail loss, Sharpe, Sortino, fill rate, MFE,
MAE, regime breakdown.

Every function here is a pure computation over a list of closed
TradeResult records (produced by the backtest engine) — no I/O, no
reliance on any other module's internal state. Each metric is
independently testable against a hand-computed value (see
tests/unit/test_backtest_metrics.py).

"R" throughout means realized profit/loss expressed as a multiple of
the trade's own initial risk (R = entry-to-stop distance in price
terms); a TradeResult's `realized_r` is computed by the engine from
the actual partial-exit fills (fills.py) and stop/TP levels, already
expressing the outcome in R terms — this module consumes that value
directly rather than re-deriving it from raw prices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import pstdev


@dataclass(frozen=True)
class TradeResult:
    """One closed trade's outcome, as produced by the backtest engine."""

    symbol: str
    strategy_source: str
    direction: str
    realized_r: float  # net of costs, in R multiples (can be negative)
    was_filled: bool  # False if the limit order was never filled (no trade occurred)
    mfe_r: float  # maximum favorable excursion, in R, over the trade's life
    mae_r: float  # maximum adverse excursion, in R (negative or zero)
    regime_label: str  # caller-defined regime bucket (e.g. "trending" / "ranging")
    opened_ts_ms: int
    closed_ts_ms: int


@dataclass(frozen=True)
class MetricsReport:
    trade_count: int
    filled_count: int
    fill_rate: float
    win_rate: float
    gross_r: float
    net_r: float
    avg_r: float
    expectancy: float
    profit_factor: float
    max_drawdown_r: float
    max_consecutive_losses: int
    tail_loss_r: float  # worst single-trade R
    sharpe: float
    sortino: float
    avg_mfe_r: float
    avg_mae_r: float
    regime_breakdown: dict[str, "MetricsReport"]  # recursive, built without the breakdown itself


def _filled_trades(trades: list[TradeResult]) -> list[TradeResult]:
    return [t for t in trades if t.was_filled]


def compute_fill_rate(trades: list[TradeResult]) -> float:
    if not trades:
        return 0.0
    return len(_filled_trades(trades)) / len(trades)


def compute_win_rate(trades: list[TradeResult]) -> float:
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    wins = sum(1 for t in filled if t.realized_r > 0)
    return wins / len(filled)


def compute_gross_r(trades: list[TradeResult]) -> float:
    """Sum of all positive-R trades only (gross profit, in R)."""
    return sum(t.realized_r for t in _filled_trades(trades) if t.realized_r > 0)


def compute_gross_loss_r(trades: list[TradeResult]) -> float:
    """Sum of all negative-R trades only (gross loss, in R; this is a
    negative number or zero). Used internally by profit_factor."""
    return sum(t.realized_r for t in _filled_trades(trades) if t.realized_r < 0)


def compute_net_r(trades: list[TradeResult]) -> float:
    return sum(t.realized_r for t in _filled_trades(trades))


def compute_avg_r(trades: list[TradeResult]) -> float:
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    return compute_net_r(trades) / len(filled)


def compute_expectancy(trades: list[TradeResult]) -> float:
    """Expectancy = win_rate * avg_win_r + loss_rate * avg_loss_r.
    Mathematically equal to avg_r for the same trade set, but computed
    independently here (via win/loss averages) so a test can verify the
    two formulations agree — a useful internal consistency check, not
    a different quantity."""
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    wins = [t.realized_r for t in filled if t.realized_r > 0]
    losses = [t.realized_r for t in filled if t.realized_r <= 0]
    win_rate = len(wins) / len(filled)
    loss_rate = len(losses) / len(filled)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return win_rate * avg_win + loss_rate * avg_loss


def compute_profit_factor(trades: list[TradeResult]) -> float:
    """Gross profit / abs(gross loss). Returns math.inf if there are
    wins and zero losses (undefined upper bound, not an error); returns
    0.0 if there are no filled trades at all."""
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    gross_profit = compute_gross_r(trades)
    gross_loss = abs(compute_gross_loss_r(trades))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_max_drawdown_r(trades: list[TradeResult]) -> float:
    """Max drawdown in R, computed on the cumulative-R equity curve in
    trade-close order. Returns a non-negative number representing the
    largest peak-to-trough decline (0.0 if the curve never declines)."""
    filled = sorted(_filled_trades(trades), key=lambda t: t.closed_ts_ms)
    if not filled:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in filled:
        cumulative += t.realized_r
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_dd = max(max_dd, drawdown)
    return max_dd


def compute_max_consecutive_losses(trades: list[TradeResult]) -> int:
    filled = sorted(_filled_trades(trades), key=lambda t: t.closed_ts_ms)
    max_streak = 0
    current_streak = 0
    for t in filled:
        if t.realized_r < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def compute_tail_loss_r(trades: list[TradeResult]) -> float:
    """The single worst (most negative) trade's R. Returns 0.0 if there
    are no filled trades or no losing trades (never a positive number —
    a tail LOSS that happens to be nonexistent is reported as 0.0, not
    as the best winning trade)."""
    filled = _filled_trades(trades)
    losses = [t.realized_r for t in filled if t.realized_r < 0]
    if not losses:
        return 0.0
    return min(losses)


def compute_sharpe(trades: list[TradeResult]) -> float:
    """Sharpe ratio on the per-trade R series: mean(R) / stdev(R).
    Returns 0.0 if fewer than 2 filled trades (stdev undefined) or if
    stdev is exactly 0 (all trades identical R — no variance to divide
    by, and a "ratio" would be either undefined or infinite depending
    on sign; 0.0 is the conservative, non-misleading choice)."""
    filled = _filled_trades(trades)
    if len(filled) < 2:
        return 0.0
    r_values = [t.realized_r for t in filled]
    mean_r = sum(r_values) / len(r_values)
    sigma = pstdev(r_values)
    if sigma == 0:
        return 0.0
    return mean_r / sigma


def compute_sortino(trades: list[TradeResult]) -> float:
    """Sortino ratio: mean(R) / downside-deviation(R), where downside
    deviation only considers trades with R < 0 (standard Sortino
    definition — upside variance is not penalized). Returns 0.0 if
    fewer than 2 filled trades, or if there are no losing trades
    (downside deviation undefined/zero — conservatively 0.0, not
    infinity, consistent with compute_sharpe's zero-variance
    handling)."""
    filled = _filled_trades(trades)
    if len(filled) < 2:
        return 0.0
    r_values = [t.realized_r for t in filled]
    mean_r = sum(r_values) / len(r_values)
    downside = [r for r in r_values if r < 0]
    if not downside:
        return 0.0
    downside_deviation = math.sqrt(sum(r * r for r in downside) / len(downside))
    if downside_deviation == 0:
        return 0.0
    return mean_r / downside_deviation


def compute_avg_mfe_r(trades: list[TradeResult]) -> float:
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    return sum(t.mfe_r for t in filled) / len(filled)


def compute_avg_mae_r(trades: list[TradeResult]) -> float:
    filled = _filled_trades(trades)
    if not filled:
        return 0.0
    return sum(t.mae_r for t in filled) / len(filled)


def compute_metrics(trades: list[TradeResult], *, include_regime_breakdown: bool = True) -> MetricsReport:
    """Compute every metric over `trades`. If `include_regime_breakdown`
    is True, also computes a full MetricsReport per distinct
    `regime_label` present in `trades` (with their own
    regime_breakdown left empty, to avoid unbounded recursion — the
    breakdown is one level deep by design)."""
    regime_breakdown: dict[str, MetricsReport] = {}
    if include_regime_breakdown:
        regimes = sorted({t.regime_label for t in trades})
        for regime in regimes:
            regime_trades = [t for t in trades if t.regime_label == regime]
            regime_breakdown[regime] = compute_metrics(regime_trades, include_regime_breakdown=False)

    return MetricsReport(
        trade_count=len(trades),
        filled_count=len(_filled_trades(trades)),
        fill_rate=compute_fill_rate(trades),
        win_rate=compute_win_rate(trades),
        gross_r=compute_gross_r(trades),
        net_r=compute_net_r(trades),
        avg_r=compute_avg_r(trades),
        expectancy=compute_expectancy(trades),
        profit_factor=compute_profit_factor(trades),
        max_drawdown_r=compute_max_drawdown_r(trades),
        max_consecutive_losses=compute_max_consecutive_losses(trades),
        tail_loss_r=compute_tail_loss_r(trades),
        sharpe=compute_sharpe(trades),
        sortino=compute_sortino(trades),
        avg_mfe_r=compute_avg_mfe_r(trades),
        avg_mae_r=compute_avg_mae_r(trades),
        regime_breakdown=regime_breakdown,
    )
