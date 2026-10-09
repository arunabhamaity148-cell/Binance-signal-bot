# Backtesting

The event-driven engine reuses strategy, consensus, veto and signal code. It enforces no-look-ahead and next-bar fill rules. Auxiliary orderbook, taker-flow and derivatives data must be supplied for realistic strategy coverage; missing inputs fail closed.

Backtests are research evidence, not live performance. Run the relevant CLI with deterministic local data and inspect assumptions, costs, latency and sample size before interpreting results.
