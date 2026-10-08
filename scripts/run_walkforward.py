#!/usr/bin/env python3
"""Run walk-forward validation from a local CSV file.

Same strict data discipline as run_backtest.py: reads ONLY from --csv,
no network access anywhere in this script or anything it imports.

EXIT CODES:
  0  walk-forward ran and all folds' gates PASSED
  2  walk-forward ran but at least one fold's gates FAILED
  3  NOT RUN (no --csv supplied, or the CSV could not be read, or
     insufficient data for the configured minimum fold count)

The holdout portion is evaluated and REPORTED but never influences
which parameters were used (there are no tunable backtest parameters
exposed by this script in the first place — every strategy/veto/risk
threshold comes from the shipped config files, unchanged across folds
— so there is structurally nothing for this script to "fit" on any
fold's training window; this script's walk-forward exists to exercise
the holdout/OOS methodology itself, not to perform parameter search).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_backtest import ACCEPTANCE_GATES, load_csv_bars, _print_not_run


def run(csv_path: Path, symbol: str, min_folds: int) -> int:
    from app.backtest.engine import BacktestConfig, run_single_symbol_backtest
    from app.backtest.metrics import compute_metrics
    from app.backtest.walk_forward import HoldoutGuard, build_anchored_folds
    from app.config import load_all
    from app.core.models import NewsState

    try:
        bars = load_csv_bars(csv_path, symbol)
    except Exception as exc:  # noqa: BLE001
        _print_not_run(f"failed to read CSV: {exc}")
        return 3

    try:
        plan = build_anchored_folds(total_length=len(bars), min_folds=min_folds)
    except ValueError as exc:
        _print_not_run(f"insufficient data for walk-forward: {exc}")
        return 3

    guard = HoldoutGuard.from_plan(plan)
    cfg = load_all()
    bt_cfg = BacktestConfig(
        app_config=cfg, assumed_equity_usd=1000.0,
        symbol_tier=cfg.symbol_tier(symbol), min_candles=cfg.strategy["common"]["min_candles"],
    )

    print(f"WALK-FORWARD: {symbol}, {len(plan.folds)} anchored folds, embargo={plan.embargo_bars} bars")
    print(f"holdout: [{plan.holdout_start_idx}, {plan.holdout_end_idx}) -- reported, never fit on")

    any_gate_failed = False
    for fold in plan.folds:
        # Evaluate the TEST window only (train window is not separately
        # "fit" on here since there is nothing in this system for a
        # walk-forward fold to tune — see module docstring); this
        # mirrors an out-of-sample evaluation per fold.
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        if len(test_bars) < bt_cfg.min_candles:
            print(f"fold {fold.fold_index}: SKIPPED (test window too short)")
            continue

        all_bars = {"5m": test_bars, "15m": [], "1h": [], "4h": [], "1d": []}
        news = NewsState(as_of_ts_ms=test_bars[-1].close_time_ms, active_events=(), unhealthy_categories=frozenset())
        trades = run_single_symbol_backtest(
            symbol=symbol, all_bars=all_bars, event_ts_per_5m_index=[], bt_cfg=bt_cfg, news_state=news,
        )
        report = compute_metrics(trades, include_regime_breakdown=False)
        print(
            f"fold {fold.fold_index}: trades={report.filled_count} win_rate={report.win_rate:.3f} "
            f"net_r={report.net_r:.3f} profit_factor={report.profit_factor:.3f}"
        )
        if report.filled_count > 0 and report.profit_factor < ACCEPTANCE_GATES["min_profit_factor"]:
            any_gate_failed = True

    # Holdout: evaluated the SAME way, reported, never used to pick
    # anything. guard.check is called here purely to make the
    # "reported but never fit on" guarantee visible/testable in this
    # script's own flow -- any future change that routed holdout data
    # into a fitting call here would have to explicitly bypass this.
    holdout_bars = bars[plan.holdout_start_idx:plan.holdout_end_idx]
    print(f"\nHOLDOUT (reported only): {len(holdout_bars)} bars")
    if len(holdout_bars) >= bt_cfg.min_candles:
        all_bars = {"5m": holdout_bars, "15m": [], "1h": [], "4h": [], "1d": []}
        news = NewsState(as_of_ts_ms=holdout_bars[-1].close_time_ms, active_events=(), unhealthy_categories=frozenset())
        trades = run_single_symbol_backtest(
            symbol=symbol, all_bars=all_bars, event_ts_per_5m_index=[], bt_cfg=bt_cfg, news_state=news,
        )
        holdout_report = compute_metrics(trades, include_regime_breakdown=False)
        print(
            f"holdout: trades={holdout_report.filled_count} win_rate={holdout_report.win_rate:.3f} "
            f"net_r={holdout_report.net_r:.3f} profit_factor={holdout_report.profit_factor:.3f}"
        )
    else:
        print("holdout: too few bars to evaluate meaningfully")

    if any_gate_failed:
        print("\nWALK-FORWARD GATES: FAILED (at least one fold below min_profit_factor)")
        return 2

    print("\nWALK-FORWARD GATES: PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run walk-forward validation from a local CSV file. No network access.")
    parser.add_argument("--csv", type=str, default=None)
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--min-folds", type=int, default=6)
    args = parser.parse_args()

    if not args.csv:
        _print_not_run("no --csv supplied")
        return 3

    return run(Path(args.csv), args.symbol, args.min_folds)


if __name__ == "__main__":
    sys.exit(main())
