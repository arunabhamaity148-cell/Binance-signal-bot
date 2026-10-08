#!/usr/bin/env python3
"""Run an offline single-symbol backtest from the local JSONL harness format."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def _not_run(reason: str) -> int:
    sys.stdout.write("NOT RUN\n")
    sys.stderr.write(f"reason: {reason}\n")
    return 3

def main() -> int:
    parser=argparse.ArgumentParser(description="Run a local JSONL backtest; no download or live I/O.")
    parser.add_argument("--jsonl", default=None, help="JSONL file in app.backtest.harness format")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--assumed-equity-usd", type=float, default=1000.0,
                        help="explicit notional assumption used by modeled costs (default 1000; not account data)")
    args=parser.parse_args()
    if not args.jsonl: return _not_run("no --jsonl supplied")
    try:
        from app.backtest.harness import load_bars_jsonl
        from app.backtest.engine import BacktestConfig, run_single_symbol_backtest
        from app.backtest.metrics import compute_metrics
        from app.config import load_all
        from app.core.models import NewsState
        replay=load_bars_jsonl(args.jsonl,symbol=args.symbol)
        cfg=load_all()
        news=NewsState(as_of_ts_ms=replay.bars[-1].close_time_ms,active_events=(),unhealthy_categories=frozenset())
        bt_cfg=BacktestConfig(app_config=cfg,assumed_equity_usd=args.assumed_equity_usd,
            symbol_tier=cfg.symbol_tier(args.symbol),min_candles=cfg.strategy["common"]["min_candles"])
        trades=run_single_symbol_backtest(symbol=args.symbol,all_bars=replay.all_bars or {"5m":replay.bars},
            event_ts_per_5m_index=[b.close_time_ms for b in replay.bars],bt_cfg=bt_cfg,news_state=news,
            orderbook_at_index=replay.orderbook_at_index,taker_flow_at_index=replay.taker_flow_at_index,
            derivatives_at_index=replay.derivatives_at_index)
        report=compute_metrics(trades)
    except Exception as exc:  # malformed local input/config is NOT RUN, fail closed
        return _not_run(f"failed to load or replay JSONL: {exc}")
    sys.stdout.write(
        f"BACKTEST COMPLETE: {args.symbol}\n"
        f"  trades:                 {report.trade_count}\n"
        f"  filled:                 {report.filled_count}\n"
        f"  fill_rate:              {report.fill_rate:.3f}\n"
        f"  net_r:                  {report.net_r:.3f}\n"
        f"  avg_r:                  {report.avg_r:.3f}\n"
        f"  profit_factor:          {report.profit_factor:.3f}\n"
    )
    return 0

if __name__=="__main__":
    raise SystemExit(main())
