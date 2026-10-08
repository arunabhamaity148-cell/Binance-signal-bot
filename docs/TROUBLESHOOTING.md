# Troubleshooting (as built)

## Configuration and static checks

- `python scripts/validate_config.py` validates the six config files, not service connectivity.
- `pytest -q` reproduces deterministic unit/integration failures; retain raw failure output.
- Run all three scanners: `scan_signal_only.py`, `scan_no_placeholders.py`, and `scan_forbidden_calls.py`. Static checks do not prove safety under every runtime condition.
- `python -m compileall app/` checks Python compilation, not behavior.
- If config validation rejects `binance_env`, use exactly `mainnet` or the explicitly selected `testnet`; do not auto-switch.
- The `paper_trading_assumptions` block is descriptive. It must not silently replace the top-level `risk_per_trade_pct: 0.5` or the `ASSUMED_ACCOUNT_EQUITY_USD` environment input.

## Standalone healthcheck

Run `python scripts/healthcheck.py`. It validates config, checks Telegram limit configuration, opens the configured production SQLite path read-only, and reads present runtime journals. It supplies no live feed, news-source, or Telegram-queue state to the health builder, so those component statuses are `unknown`. If the journal is readable, the aggregate is `UNKNOWN` and the script returns 1 because live component inputs were not supplied. If the journal is unavailable, the aggregate is `UNHEALTHY` and the script returns 3 after earlier checks pass. Exit 1 also covers config/Telegram validation failures. This check cannot establish actual current feed/news/queue health.

## Offline smoke test

Run `ASSUMED_ACCOUNT_EQUITY_USD=2400 python scripts/smoke_test.py [--symbol BTCUSDT] [--journal runtime/smoke_journal.sqlite3]`. The finite positive assumed-equity value is required for signal sizing; it is not an exchange balance. The script feeds a synthetic offline fixture through real strategy/consensus/veto/signal construction and writes to a separate smoke journal. It makes no external network calls and refuses the production DB path. A pass tests the local synthetic pipeline, not current market data, live feeds, Telegram delivery, or exchange connectivity.

## Soak observer and canary

- `python scripts/soak_test.py` observes its own process resource usage and records explicit `unknown` entries for feed freshness, signal-delivery latency, veto rate, duplicates, and restart recovery because it receives no live bot stream/state. A real soak report is complete only after at least 72 wall-clock hours; shorter `--hours` runs are test/partial reports. It does **not** run or supervise the live bot.
- `python scripts/canary.py` uses the synthetic offline pipeline and an isolated journal; symbol or daily-cap scope is mutually exclusive. External delivery is disabled. It is not a live canary deployment.

## Paper outcomes and daily report

- Record only a manually reconciled outcome for an existing signal: `python scripts/record_outcome.py --signal-id CSB-... --realized-r 1.25 --note "operator note"`. Positive R is a gain; negative R is a loss. The command cannot observe exchange fills. It enforces one manual row per signal.
- Preview today's IST report without Telegram: `python scripts/daily_report.py --dry-run`. Use `--db PATH` only for isolated tests. The background bot task schedules a daily report at 23:59 Asia/Kolkata; it reads persisted rows only.
- The displayed P&L assumes ₹5,000 per R, while the unchanged top-level risk percentage is 0.5% and signal sizing uses `ASSUMED_ACCOUNT_EQUITY_USD`. Do not treat reported assumed P&L as actual exchange P&L or infer that the risk assumption matches position sizing.
- Report send/generation failures are logged and the scheduler continues. A dry-run output does not prove Telegram deliverability.

## Error notifications

When `TELEGRAM_DRY_RUN=false`, configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and the distinct `TELEGRAM_ERROR_CHAT_ID`. ERROR/CRITICAL log records after notifier initialization are queued for database persistence; Telegram sends are rate-limited to one per five minutes per process. The in-memory rate state resets on restart. If the error queue is full or persistence/Telegram fails, the notifier writes a concise message to stderr and does not intentionally raise into the market-data loop. Startup errors before notifier initialization are not persisted by this handler. Inspect the service's stdout/stderr or the operator-managed log destination; the repository does not configure a rotating log file.

## Backtest inputs

CSV runners require `--csv`; without it they print `NOT RUN` and exit 3. CSV contains OHLCV only, so auxiliary order-book/taker/derivative contexts are absent and the fail-closed strategies may yield zero candidates. JSONL replay is available through `scripts/run_backtest_jsonl.py --jsonl FILE`; JSONL can carry bars, order-book, taker flow, derivatives and higher timeframes. It remains an offline simulation, not live fill validation.

## Runtime failures and testnet

`python -m app.main` performs configuration, assumed-equity and credential checks, public exchange-info validation, a bounded first-snapshot wait, and then news/evaluation startup. Inspect logs for stage and symbol-level failures. Public market-data, news-source, or Telegram operations have not been tested end to end here. Do not bypass a missing feed, stale-input check, veto, or safety guard to make a run continue.

`config/system.yaml` defaults to `binance_env: mainnet`. `testnet` is opt-in and emits an explicit warning that data may be sparse and must not be used for signal generation. It is not a repair mode for mainnet errors. Preserve raw logs and reproduce problems in an isolated environment; there is no verified live deployment or automatic incident supervisor.
