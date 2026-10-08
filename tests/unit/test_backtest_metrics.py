"""Each metric tested against a value computed BY HAND (shown in the
comments) independently of app/backtest/metrics.py's implementation,
using a fixed, small trade set:

    R values (in trade-close order): [2.0, -1.0, 1.5, -1.0, -1.0, 3.0]

Hand-computed expectations:
    win_rate            = 3/6 = 0.5
    gross_r             = 2.0 + 1.5 + 3.0 = 6.5
    net_r               = 2.0 - 1.0 + 1.5 - 1.0 - 1.0 + 3.0 = 3.5
    avg_r               = 3.5 / 6 = 0.58333...
    profit_factor       = 6.5 / 3.0 = 2.16666...
    expectancy          = win_rate*avg_win + loss_rate*avg_loss
                         = 0.5*(6.5/3) + 0.5*(-3.0/3) = 0.58333... (equals avg_r)
    cumulative curve    = [2.0, 1.0, 2.5, 1.5, 0.5, 3.5]
    max_drawdown_r      = peak 2.5 -> trough 0.5 = 2.0
    max_consecutive_losses = 2 (the two -1.0s in a row at positions 4,5)
    tail_loss_r         = -1.0 (worst single trade)
    sharpe              = mean(R)/pstdev(R) = 0.58333/1.64359 = 0.35491...
    sortino             = mean(R)/downside_dev = 0.58333/1.0 = 0.58333...
        (downside_dev = sqrt(mean of squared negative R's) = sqrt((1+1+1)/3) = 1.0)
"""
from __future__ import annotations

import math

import pytest

from app.backtest.metrics import (
    TradeResult,
    compute_avg_mae_r,
    compute_avg_mfe_r,
    compute_avg_r,
    compute_expectancy,
    compute_fill_rate,
    compute_gross_r,
    compute_max_consecutive_losses,
    compute_max_drawdown_r,
    compute_metrics,
    compute_net_r,
    compute_profit_factor,
    compute_sharpe,
    compute_sortino,
    compute_tail_loss_r,
    compute_win_rate,
)


def _trade(r, ts, filled=True, mfe=None, mae=None, regime="default"):
    return TradeResult(
        symbol="BTCUSDT", strategy_source="S1", direction="LONG",
        realized_r=r, was_filled=filled,
        mfe_r=mfe if mfe is not None else max(r, 0),
        mae_r=mae if mae is not None else min(r, 0),
        regime_label=regime, opened_ts_ms=ts - 1, closed_ts_ms=ts,
    )


_KNOWN_TRADES = [
    _trade(2.0, 1), _trade(-1.0, 2), _trade(1.5, 3),
    _trade(-1.0, 4), _trade(-1.0, 5), _trade(3.0, 6),
]


def test_win_rate_hand_computed():
    assert compute_win_rate(_KNOWN_TRADES) == pytest.approx(0.5)


def test_gross_r_hand_computed():
    assert compute_gross_r(_KNOWN_TRADES) == pytest.approx(6.5)


def test_net_r_hand_computed():
    assert compute_net_r(_KNOWN_TRADES) == pytest.approx(3.5)


def test_avg_r_hand_computed():
    assert compute_avg_r(_KNOWN_TRADES) == pytest.approx(3.5 / 6)


def test_profit_factor_hand_computed():
    assert compute_profit_factor(_KNOWN_TRADES) == pytest.approx(6.5 / 3.0)


def test_expectancy_hand_computed():
    assert compute_expectancy(_KNOWN_TRADES) == pytest.approx(3.5 / 6)


def test_expectancy_equals_avg_r_internal_consistency():
    """Two different formulations of the same underlying quantity must
    agree — a structural check independent of either hand computation."""
    assert compute_expectancy(_KNOWN_TRADES) == pytest.approx(compute_avg_r(_KNOWN_TRADES))


def test_max_drawdown_hand_computed():
    assert compute_max_drawdown_r(_KNOWN_TRADES) == pytest.approx(2.0)


def test_max_consecutive_losses_hand_computed():
    assert compute_max_consecutive_losses(_KNOWN_TRADES) == 2


def test_tail_loss_hand_computed():
    assert compute_tail_loss_r(_KNOWN_TRADES) == pytest.approx(-1.0)


def test_sharpe_hand_computed():
    assert compute_sharpe(_KNOWN_TRADES) == pytest.approx(0.3549140885943757, rel=1e-6)


def test_sortino_hand_computed():
    assert compute_sortino(_KNOWN_TRADES) == pytest.approx(3.5 / 6, rel=1e-6)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_fill_rate_all_filled():
    assert compute_fill_rate(_KNOWN_TRADES) == 1.0


def test_fill_rate_with_unfilled_trades():
    trades = _KNOWN_TRADES + [_trade(0.0, 7, filled=False), _trade(0.0, 8, filled=False)]
    assert compute_fill_rate(trades) == pytest.approx(6 / 8)


def test_unfilled_trades_excluded_from_win_rate_and_r_metrics():
    trades = _KNOWN_TRADES + [_trade(999.0, 7, filled=False)]  # absurd R, must be ignored
    assert compute_win_rate(trades) == pytest.approx(0.5)
    assert compute_net_r(trades) == pytest.approx(3.5)


def test_empty_trade_list_all_metrics_safe():
    assert compute_fill_rate([]) == 0.0
    assert compute_win_rate([]) == 0.0
    assert compute_gross_r([]) == 0.0
    assert compute_net_r([]) == 0.0
    assert compute_avg_r([]) == 0.0
    assert compute_expectancy([]) == 0.0
    assert compute_profit_factor([]) == 0.0
    assert compute_max_drawdown_r([]) == 0.0
    assert compute_max_consecutive_losses([]) == 0
    assert compute_tail_loss_r([]) == 0.0
    assert compute_sharpe([]) == 0.0
    assert compute_sortino([]) == 0.0


def test_no_losses_tail_loss_is_zero_not_a_win():
    trades = [_trade(1.0, 1), _trade(2.0, 2)]
    assert compute_tail_loss_r(trades) == 0.0


def test_no_losses_profit_factor_is_infinite():
    trades = [_trade(1.0, 1), _trade(2.0, 2)]
    assert compute_profit_factor(trades) == math.inf


def test_no_wins_no_losses_profit_factor_zero():
    assert compute_profit_factor([]) == 0.0


def test_single_trade_sharpe_and_sortino_are_zero():
    """Fewer than 2 filled trades -> stdev undefined -> 0.0, not an
    error and not a misleading large number."""
    trades = [_trade(2.0, 1)]
    assert compute_sharpe(trades) == 0.0
    assert compute_sortino(trades) == 0.0


def test_zero_variance_sharpe_is_zero():
    trades = [_trade(1.0, 1), _trade(1.0, 2), _trade(1.0, 3)]
    assert compute_sharpe(trades) == 0.0


def test_no_downside_sortino_is_zero():
    trades = [_trade(1.0, 1), _trade(2.0, 2)]
    assert compute_sortino(trades) == 0.0


def test_max_drawdown_zero_for_monotonically_increasing_curve():
    trades = [_trade(1.0, 1), _trade(1.0, 2), _trade(1.0, 3)]
    assert compute_max_drawdown_r(trades) == 0.0


def test_max_consecutive_losses_zero_for_all_wins():
    trades = [_trade(1.0, 1), _trade(2.0, 2)]
    assert compute_max_consecutive_losses(trades) == 0


def test_drawdown_uses_closed_ts_order_not_list_order():
    """Trades passed out of chronological order must still be
    evaluated in true close-time order for drawdown purposes."""
    out_of_order = [_trade(-1.0, 5), _trade(2.0, 1), _trade(-1.0, 3)]
    # true order by ts: 2.0 (ts1), -1.0 (ts3), -1.0 (ts5) -> curve [2,1,0], dd = 2
    assert compute_max_drawdown_r(out_of_order) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# MFE / MAE
# ---------------------------------------------------------------------------


def test_avg_mfe_hand_computed():
    trades = [_trade(1.0, 1, mfe=2.0), _trade(-1.0, 2, mfe=0.5)]
    assert compute_avg_mfe_r(trades) == pytest.approx(1.25)


def test_avg_mae_hand_computed():
    trades = [_trade(1.0, 1, mae=-0.3), _trade(-1.0, 2, mae=-1.2)]
    assert compute_avg_mae_r(trades) == pytest.approx(-0.75)


# ---------------------------------------------------------------------------
# Regime breakdown
# ---------------------------------------------------------------------------


def test_regime_breakdown_splits_correctly():
    trades = [
        _trade(2.0, 1, regime="trending"), _trade(-1.0, 2, regime="trending"),
        _trade(1.0, 3, regime="ranging"),
    ]
    report = compute_metrics(trades)
    assert set(report.regime_breakdown.keys()) == {"trending", "ranging"}
    assert report.regime_breakdown["trending"].trade_count == 2
    assert report.regime_breakdown["ranging"].trade_count == 1
    assert report.regime_breakdown["trending"].net_r == pytest.approx(1.0)


def test_regime_breakdown_not_recursive_beyond_one_level():
    trades = [_trade(1.0, 1, regime="trending")]
    report = compute_metrics(trades)
    assert report.regime_breakdown["trending"].regime_breakdown == {}


def test_regime_breakdown_can_be_disabled():
    trades = [_trade(1.0, 1, regime="trending")]
    report = compute_metrics(trades, include_regime_breakdown=False)
    assert report.regime_breakdown == {}


def test_compute_metrics_full_report_matches_individual_functions():
    report = compute_metrics(_KNOWN_TRADES, include_regime_breakdown=False)
    assert report.trade_count == 6
    assert report.filled_count == 6
    assert report.win_rate == pytest.approx(compute_win_rate(_KNOWN_TRADES))
    assert report.net_r == pytest.approx(compute_net_r(_KNOWN_TRADES))
    assert report.max_drawdown_r == pytest.approx(compute_max_drawdown_r(_KNOWN_TRADES))
    assert report.sharpe == pytest.approx(compute_sharpe(_KNOWN_TRADES))
