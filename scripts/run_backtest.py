#!/usr/bin/env python3
"""Run a backtest from a local CSV file.

STRICT DATA DISCIPLINE (spec section 22 / explicit instruction):
  - Reads ONLY from --csv. No network access anywhere in this script
    or in anything it imports from app.backtest.
  - WITHOUT --csv: prints "NOT RUN" and exits 3. Never invents, never
    downloads, never falls back to any other data source.

CSV FORMAT: header row `ts_ms,open,high,low,close,volume` (oldest-first).
Optional additional columns are NOT supported by the CSV path (use
harness.py's JSONL format directly, via a small conversion script, if
auxiliary order-book/taker-flow data is needed) — without that data,
every strategy will correctly produce zero candidates (fail-closed,
not a bug; see app/backtest/engine.py's docstring).

EXIT CODES:
  0  backtest ran and acceptance gates (spec section 22) PASSED
  2  backtest ran but acceptance gates FAILED
  3  NOT RUN (no --csv supplied, or the CSV could not be read)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Acceptance gates per spec section 22. Values are the exact figures
# given in the spec; class is informational only (these are PASS/FAIL
# criteria the spec itself defines, not independently-classed
# thresholds like the strategy/veto config).
ACCEPTANCE_GATES = {
    "min_trades": 100,
    "min_profit_factor": 1.25,
    "min_avg_r": 0.05,
    "max_drawdown_r": 15.0,
    "min_fill_rate": 0.35,
    "min_regime_r": -0.15,
}


def _print_not_run(reason: str) -> None:
    print("NOT RUN")
    print(f"reason: {reason}", file=sys.stderr)


def load_csv_bars(csv_path: Path, symbol: str):
    from app.core.errors import DataIntegrityError
    from app.core.math import OHLC

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    bars = []
    prev_ts = None
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"ts_ms", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise DataIntegrityError(
                f"{csv_path}: CSV header must contain columns {sorted(required)}, "
                f"got {reader.fieldnames}"
            )
        for lineno, row in enumerate(reader, start=2):  # header is line 1
            try:
                ts_ms = int(row["ts_ms"])
            except (TypeError, ValueError) as exc:
                raise DataIntegrityError(f"{csv_path}:{lineno}: invalid ts_ms: {exc}") from exc
            if prev_ts is not None and ts_ms <= prev_ts:
                raise DataIntegrityError(f"{csv_path}:{lineno}: out-of-order or duplicate ts_ms {ts_ms}")
            prev_ts = ts_ms
            try:
                bar = OHLC(
                    open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                    close=float(row["close"]), volume=float(row["volume"]), close_time_ms=ts_ms,
                )
            except ValueError as exc:
                raise DataIntegrityError(f"{csv_path}:{lineno}: invalid OHLC values: {exc}") from exc
            bars.append(bar)

    if not bars:
        raise DataIntegrityError(f"{csv_path}: no valid bar rows found")
    return bars


def run(csv_path: Path, symbol: str) -> int:
    from app.backtest.engine import BacktestConfig, run_single_symbol_backtest
    from app.backtest.metrics import compute_metrics
    from app.config import load_all
    from app.core.models import NewsState

    try:
        bars = load_csv_bars(csv_path, symbol)
    except Exception as exc:  # noqa: BLE001 - any read/parse failure is NOT RUN, not a crash
        _print_not_run(f"failed to read CSV: {exc}")
        return 3

    cfg = load_all()
    all_bars = {"5m": bars, "15m": [], "1h": [], "4h": [], "1d": []}
    news = NewsState(as_of_ts_ms=bars[-1].close_time_ms, active_events=(), unhealthy_categories=frozenset())
    bt_cfg = BacktestConfig(
        app_config=cfg, assumed_equity_usd=1000.0,
        symbol_tier=cfg.symbol_tier(symbol), min_candles=cfg.strategy["common"]["min_candles"],
    )

    trades = run_single_symbol_backtest(
        symbol=symbol, all_bars=all_bars, event_ts_per_5m_index=[], bt_cfg=bt_cfg, news_state=news,
    )
    report = compute_metrics(trades)

    print(f"BACKTEST COMPLETE: {symbol}")
    print(f"  trades (filled):        {report.filled_count}")
    print(f"  fill_rate:              {report.fill_rate:.3f}")
    print(f"  win_rate:               {report.win_rate:.3f}")
    print(f"  net_r:                  {report.net_r:.3f}")
    print(f"  avg_r:                  {report.avg_r:.3f}")
    print(f"  profit_factor:          {report.profit_factor:.3f}")
    print(f"  max_drawdown_r:         {report.max_drawdown_r:.3f}")
    print(f"  sharpe:                 {report.sharpe:.3f}")
    print(f"  sortino:                {report.sortino:.3f}")

    gate_failures = []
    if report.filled_count < ACCEPTANCE_GATES["min_trades"]:
        gate_failures.append(f"trades {report.filled_count} < {ACCEPTANCE_GATES['min_trades']}")
    if report.profit_factor < ACCEPTANCE_GATES["min_profit_factor"]:
        gate_failures.append(f"profit_factor {report.profit_factor:.3f} < {ACCEPTANCE_GATES['min_profit_factor']}")
    if report.avg_r < ACCEPTANCE_GATES["min_avg_r"]:
        gate_failures.append(f"avg_r {report.avg_r:.3f} < {ACCEPTANCE_GATES['min_avg_r']}")
    if report.max_drawdown_r > ACCEPTANCE_GATES["max_drawdown_r"]:
        gate_failures.append(f"max_drawdown_r {report.max_drawdown_r:.3f} > {ACCEPTANCE_GATES['max_drawdown_r']}")
    if report.fill_rate < ACCEPTANCE_GATES["min_fill_rate"]:
        gate_failures.append(f"fill_rate {report.fill_rate:.3f} < {ACCEPTANCE_GATES['min_fill_rate']}")
    for regime, regime_report in report.regime_breakdown.items():
        if regime_report.filled_count > 0 and regime_report.avg_r < ACCEPTANCE_GATES["min_regime_r"]:
            gate_failures.append(f"regime {regime!r} avg_r {regime_report.avg_r:.3f} < {ACCEPTANCE_GATES['min_regime_r']}")

    if gate_failures:
        print("\nACCEPTANCE GATES: FAILED")
        for f in gate_failures:
            print(f"  - {f}")
        return 2

    print("\nACCEPTANCE GATES: PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a backtest from a local CSV file. No network access.")
    parser.add_argument("--csv", type=str, default=None, help="Path to a CSV file with columns ts_ms,open,high,low,close,volume")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Symbol label for this backtest run")
    args = parser.parse_args()

    if not args.csv:
        _print_not_run("no --csv supplied")
        return 3

    return run(Path(args.csv), args.symbol)


if __name__ == "__main__":
    sys.exit(main())
