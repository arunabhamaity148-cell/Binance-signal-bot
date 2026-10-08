# Backtesting and replay (as built)

All included runners use local inputs; they do not download data or access a live exchange. A replay result is not live-fill evidence.

## JSONL replay

```bash
python scripts/run_backtest_jsonl.py --jsonl path/to/replay.jsonl --symbol BTCUSDT
```

`app/backtest/harness.py` reads one oldest-first 5m JSON object per line, with required `ts_ms/open/high/low/close/volume`; optional per-row `orderbook`, `taker_flow`, complete-to-date `derivatives` histories, and closed higher-timeframe bars in `timeframes`. Timestamps must be ordered; future-dated series or higher-timeframe bars are rejected to prevent look-ahead. Derivatives use the current `DerivativesState` field names. Missing optional data is not fabricated; strategy inputs fail closed. Assumed equity defaults to USD 1,000 for modeled sizing/costs; it is not account data. With no `--jsonl`, the runner prints `NOT RUN` and exits 3. Success prints metrics; this runner does not apply the CSV runner's acceptance-gate list.

## CSV runners (behavior preserved)

`python scripts/run_backtest.py --csv path.csv --symbol BTCUSDT` and `python scripts/run_walkforward.py --csv path.csv --symbol BTCUSDT` read only `ts_ms,open,high,low,close,volume`. CSV has no order-book, taker-flow, or derivatives fields; strategies needing them correctly fail closed and may yield no candidates. **Without `--csv`, either script prints `NOT RUN` and exits 3**—there is no default dataset, download, or fallback. Backtest exits 0 for gates passed, 2 for gates failed, 3 for not run/input failure. Walk-forward uses fixed shipped config; it does not search/tune strategy parameters and reports holdout without fitting on it.

The CSV single-run gates in `scripts/run_backtest.py` are 100 filled trades, profit factor 1.25, average R 0.05, max drawdown 15 R, fill rate 0.35, and regime average R floor -0.15 where applicable. These are script acceptance criteria, not proof of an edge.

## Cost, latency, and fill assumptions

The backtest passes `assumed_latency_s=2.0` by default into cost calculation. Latency slippage grows linearly at 0.5 bps/second (2 seconds models 1 bp), capped at 25 bps. The separate slippage model is linear in notional/depth, capped at 50 bps, with a 5 bps fallback when depth is unavailable. Neither is calibrated from observed executions.

`assess_limit_fill` estimates probability from the fraction of the entry zone overlapped by a bar's OHLC range. The engine applies it with a reproducible uniform draw: seed material is UTF-8 `signal_id + NUL + bar_close_time_ms`; SHA-256 is computed, its first eight bytes interpreted big-endian as an integer seed, then `random.Random(seed).random()` is compared with the probability. The same signal/bar therefore replays to the same fill decision. Degenerate point entries have a binary touch outcome.

This is deterministic bar-level modeling, **not live fill realism**. OHLC cannot model queue position, order-book priority, partial fills, intrabar path, stressed market impact, exchange matching, network timing variation, or human execution. Do not treat a backtest pass or seeded fill as live execution evidence.
