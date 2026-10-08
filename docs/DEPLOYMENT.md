# Deployment and operations (as built)

## Status

`app/main.py` and `app/bot.py` implement a signal-only runtime, but no live deployment or end-to-end public-feed/Telegram operation has been verified. This is an operator checklist, **not production approval**. No production-readiness or profitability claim is made.

## Offline validation

From the repository root with Python 3.11+ and dependencies installed:

```bash
python -m pip install -r requirements.txt
pytest -q
python -m compileall app/
python scripts/validate_config.py
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
ASSUMED_ACCOUNT_EQUITY_USD=2400 python scripts/smoke_test.py
python scripts/healthcheck.py
python scripts/daily_report.py --dry-run
```

Review complete output and all failures; do not skip/xfail tests or adjust class E/F values to obtain a pass. These checks do not verify external connectivity or operational safety. The daily report dry-run reads/initializes local SQLite and prints without contacting Telegram.

## Runtime entry point (not live-verified)

The Python entry point is `python -m app.main`. It requires valid config and finite positive `ASSUMED_ACCOUNT_EQUITY_USD`, checks that named Binance trading credentials are absent, fetches public `exchangeInfo`, starts public market data, requires at least the configured 15 validated symbols to become snapshot-ready within 90 seconds, starts news/evaluation, and can send Telegram messages. It performs network requests. The default `binance_env` is `mainnet`; `testnet` is opt-in for pipeline verification and prints this warning to stderr and the application log:

> TESTNET MODE — market data is sparse and may not reflect real liquidity. This is for pipeline verification only, NOT for signal generation.

Do not remove the warning or interpret testnet signals as actionable. No exchange orders are created by this code. The bot does not set leverage.

## Paper-risk assumptions

`config/risk.yaml` documents ₹200,000 assumed capital, $2,400 approximate capital, ₹5,000 assumed risk per R, 2.5% paper risk, and 10x operator-assumed leverage. These are for operator reference and daily-report conversion; they do not configure exchange leverage. Actual advisory sizing still uses the top-level `risk_per_trade_pct: 0.5` and `ASSUMED_ACCOUNT_EQUITY_USD`. Therefore the daily report's ₹5,000/R assumed P&L conversion may not equal the amount implied by advisory sizing. Reconcile these assumptions yourself before interpreting any report; see `KNOWN_UNCERTAINTIES.md`.

## Telegram, daily report, and error channel

Telegram is dry-run by default in `.env.example`. Actual delivery requires `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `TELEGRAM_DRY_RUN=false`. The error notifier additionally requires `TELEGRAM_ERROR_CHAT_ID`, a separate destination. It installs an ERROR/CRITICAL logging handler after config/equity/trading-credential checks and before public `exchangeInfo` startup, persists events to SQLite, and rate-limits notification attempts to one per five minutes per process. The rate state resets on restart. Telegram failures are fail-soft and do not terminate the market-data loop.

A background task schedules a database-backed daily report for 23:59 Asia/Kolkata. It uses only persisted signal rows, recorded guard blocks, logged error events, and operator-entered outcomes. `python scripts/daily_report.py --dry-run` is the manual non-sending check. To deliver manually, an operator must deliberately use `--send` with the live Telegram environment. `python scripts/record_outcome.py` records a manual R outcome for an existing signal; this is not exchange fill detection. Report scheduling and external delivery have not been validated against Telegram in a live deployment.

## News sources and network checks

News URLs in `config/news_sources.yaml` still include literal placeholders and must be verified before news is relied on. Before any operator-run network test, review config/environment, use a controlled runtime, confirm outbound access and observability, and ensure no exchange trading credentials are present. No such operator validation has been performed here.

## Docker and Compose

The Dockerfile creates a non-root user and makes the copied application tree read-only, but its default command is **only** `python scripts/validate_config.py`. Compose runs the same check with `network_mode: none`, a read-only filesystem, dropped capabilities, and no-new-privileges. These are validation containers, not runtime deployment manifests; they do not start `app.main` or validate live feeds/delivery.

## Persisted data and remaining work

The live bot's SQLite path is configured by `config/system.yaml` (default `data/signals.db`) and contains signal, lifecycle, manual outcome, veto-block and ERROR/CRITICAL event records. Backups, restore tests, durable host storage, service supervision, alert routing, network policy, retention/rotation, and restart recovery must be designed and validated by an operator. Standalone health, smoke, soak, and canary scripts have the limits described in `TROUBLESHOOTING.md`.
