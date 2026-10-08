# Backtesting (as built)

`app/backtest/engine.py` walks historical 5m bars, builds snapshots with bars visible through the current index, and searches for fills starting from the next bar. Strategy registry, consensus, and veto modules are reused. `BacktestConfig` has a default assumed latency of 2.0 seconds. Auxiliary order-book, taker-flow, and derivatives series can be supplied by index; OHLCV alone does not reconstruct them and required missing inputs can yield zero candidates.

Scripts shipped: `scripts/run_backtest.py` and `scripts/run_walkforward.py`. No JSONL runner is present in this repository snapshot. Backtests use modeled assumptions and are not evidence of future results or live fill realism. Inspect CSV input expectations in scripts before using.
