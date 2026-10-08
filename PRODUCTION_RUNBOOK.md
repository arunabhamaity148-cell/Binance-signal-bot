# Operations runbook (operator validation required)

## Status

This checkout contains `app/main.py` and `app/bot.py`, including a staged signal-only runtime. It has **not** been validated end to end against Binance, the configured news feeds, or Telegram, and no live deployment, paper-soak, or production-readiness claim is supported. This runbook is not approval to deploy.

## Offline validation

From the repository root with Python 3.11+:

```bash
python -m pip install -r requirements.txt
pytest -q
python -m compileall app/
python scripts/validate_config.py
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

Preserve full output and investigate every failure. Do not skip/xfail tests, weaken guards, or alter class E/F thresholds to obtain a pass. These checks do not verify external connectivity or operational safety.

## Runtime checks before any operator-run network test

1. Review the six config files, especially enabled symbols and the exact public base URLs. News source entries remain literal placeholders; do not rely on them until replaced and independently verified.
2. Set a finite positive `ASSUMED_ACCOUNT_EQUITY_USD` explicitly. It is an advisory sizing assumption, not an exchange account balance.
3. Ensure `BINANCE_API_KEY` and `BINANCE_API_SECRET` are absent. The bot deliberately refuses to start if configured forbidden trading credentials are present.
4. Confirm the expected network egress, DNS/TLS, host persistence, backups, restart policy, log retention, alert routing, and resource limits in a controlled environment.
5. Understand that `python -m app.main` opens public market-data connections, validates at least 15 of 20 configured symbols, waits up to 90 seconds for initial snapshot readiness, then schedules news/evaluation. It can publish signal notifications through Telegram if dry-run is disabled and Telegram credentials are provided. It does not place exchange orders.
6. Validate operator rollback, service stop, and data recovery separately. No live test has been performed by this task.

## Docker scope

The Dockerfile's default command is `python scripts/validate_config.py`. Compose runs that command with networking disabled, a read-only root filesystem, dropped capabilities, and no-new-privileges. They are validation-only, not a containerized runtime deployment.

## Limitations and incident response

The live bot currently uses a SQLite signal repository, normal application logs, an in-memory Telegram outbox, and background market/news tasks. Monitoring utilities are not all integrated; there is no daily Telegram performance report, dedicated Telegram error notifier, testnet configuration, or automated incident supervisor in this checkout. Standalone healthcheck, smoke, soak, and canary scripts cannot substitute for live validation. If inputs are missing/stale, a guard blocks, persistence fails, or notification behavior is unclear, stop relying on outputs, preserve logs, and investigate rather than bypassing a control.
