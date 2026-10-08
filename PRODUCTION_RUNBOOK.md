# Operations / production runbook (scope-limited)

## Release gate

This repository snapshot lacks `app/main.py`, `app/bot.py`, `app/monitoring/health.py`, metrics/journals/soak implementations, `scripts/smoke_test.py`, `scripts/healthcheck.py`, `scripts/soak_test.py`, and `scripts/canary.py`. Therefore this runbook does **not** authorize live operation. There is no verified end-to-end runtime to launch. Use it as an offline code-validation checklist only.

## Offline checks

From repository root with Python 3.11+: install `requirements.txt`; run `pytest -q`, `python -m compileall app/`, `python scripts/validate_config.py`, then the three scanners listed in README. Preserve raw output and exit codes. Investigate every failure; do not skip tests or weaken thresholds to obtain a pass.

## Operational status requirements before any future deployment

An operator must independently validate code and configuration, public market-data connectivity, symbol metadata, feed freshness, news source availability and terms, persistent state/backups, alert delivery, restart behavior, resource limits, and a wall-clock paper/shadow soak. No LIVE VERIFIED or soak claim is supported by this source snapshot. Do not supply exchange trading credentials.

## Incident response

If any signal, feed, config, persistence, or notification anomaly is observed: stop using outputs, retain logs/data, inspect the relevant failure path, and require a reviewed release before reuse. Never treat a missing feed or exception as permission to pass a guard. This repo contains no shipped automated incident supervisor.
