# Binance Signal-Only Futures Bot

A **signal-only** advisory system for Binance USDⓈ-M public market data. It evaluates five strategies (S1–S5), applies seven production risk guards, and sends reviewable signals to Telegram. The operator remains responsible for any manual action on Delta Exchange.

> **No orders are placed.** The repository contains no exchange trading credentials, account-order endpoints, leverage operations, or automated execution path.

## What it does

```text
Binance public data → snapshot → S1–S5 → consensus → 7 guards → advisory risk → signal → Telegram
```

Active guards:

| Guard | Purpose | Action |
|---|---|---|
| G1 | Data integrity | Block |
| G2 | Feed health | Block |
| G4 | Spread explosion / toxic-flow proxy | Block |
| G5 | Open-interest anomaly | Block |
| G6 | Funding extreme | Grade degrade |
| G8 | Volatility flash | Grade degrade |
| G9 | BTC regime | Grade degrade |

The former venue-specific or redundant guards were **removed from production code and configuration**, not merely disabled: G3, G7, G10, G11, G12, G13, G14, G15 and G16.

## What it does not do

- place, amend, cancel, or close orders;
- read private balances, positions, fills, margin, or liquidation state;
- set leverage or transfer funds;
- guarantee profitability or provide investment advice.

Signals contain advisory entry, stop, targets, confidence, grade, expiry, and sizing. They are not execution instructions and are not evidence of live performance.

## Installation

Python 3.11+ is recommended.

```bash
git clone https://github.com/arunabhamaity148-cell/Binance-signal-bot.git
cd Binance-signal-bot
python3.11 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

Configure environment values from `.env.example`. Keep secrets outside Git and use `TELEGRAM_DRY_RUN=true` while validating locally.

## Verification

Run from the repository root:

```bash
source venv/bin/activate
python scripts/validate_config.py
pytest -q
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

The current cleaned tree is expected to pass the full suite and all scanners. Tests are deterministic and do not prove profitability or production connectivity.

## Runtime operation

```bash
source venv/bin/activate
python -m app.main
```

The bot consumes public Binance REST/WebSocket data. Delta product metadata is optional and used only for advisory conversion. News feeds are optional enrichment; configuration-disabled feeds are skipped without outage warnings.

For VPS operations, use [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md). For the required 72-hour paper soak, use [docs/PAPER_SOAK.md](docs/PAPER_SOAK.md).

## Configuration

- `config/system.yaml` — public endpoints, timing, database and runtime policy.
- `config/strategy.yaml` — S1–S5 and consensus parameters.
- `config/veto.yaml` — exactly seven active guards.
- `config/risk.yaml` — advisory sizing and paper assumptions.
- `config/top20_pairs.yaml` — symbol and exchange-filter metadata.
- `config/news_sources.yaml` — optional news sources and health policy.
- `config/delta.yaml` — public Delta product conversion metadata.

See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) for the operator-facing reference.

## Project status

The code is suitable for controlled **paper-soak validation**, not an unattended trading promise. Production sign-off still requires a clean deployment, correct environment configuration, healthy live feeds, and completion of the 72-hour paper soak with no unexplained critical alerts.

## License and security

See [LICENSE](LICENSE) and [SECURITY.md](SECURITY.md). Report security issues privately; never commit API keys, Telegram tokens, database files, logs, or private credentials.
