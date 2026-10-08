# Binance Signal-Only Futures Bot

Version **1.0.0** (package metadata). This repository contains Python components for futures market data, candidate signals, risk/veto evaluation, news processing, and historical backtesting. It is advisory software, not an order execution system.

## Safety notice

**This software does not place, amend, cancel, or close orders, change leverage, or transfer funds.** The Binance REST client is limited to public unauthenticated read-only endpoints. Do not add exchange trading credentials or execution code. Signals and backtests are not investment advice or a promise of performance. Crypto futures are high risk and require independent human review.

The inspected checkout does **not** contain the planned live orchestration (`app/main.py`/`app/bot.py`) or operational monitoring scripts. Do not treat it as an unattended live service.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/validate_config.py
pytest -q
python -m compileall app/
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

Six YAML files live in `config/`. `ASSUMED_ACCOUNT_EQUITY_USD`, where needed, is a positive numeric assumption, not exchange-verified equity. Never commit secrets.

## Shipped capabilities

- Five registered candidate strategies (S1–S5), shared consensus/veto/risk and signal models.
- Public Binance USDⓈ-M REST and WebSocket market-data components.
- News parsing, deduplication, credibility/corroboration/impact and decay components.
- Event-driven backtest and walk-forward components and CSV-oriented scripts.
- Static safety scanners and tests.

See `docs/` and `REPO_STRUCTURE.md` for as-built details and `FINAL_RELEASE_REPORT.md` for the exact validation record. Offline checks do not establish live correctness, profitability, completed paper soak, or production readiness.
