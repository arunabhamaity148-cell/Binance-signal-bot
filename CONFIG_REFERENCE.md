# Configuration reference (as built)

The application loads six YAML mappings from `config/`; the semantic validator is `scripts/validate_config.py`.

| File | Responsibility |
|---|---|
| `top20_pairs.yaml` | Configured symbol universe and local market precision/fee assumptions. |
| `system.yaml` | Signal-only mode, public Binance URLs/environment, REST/Telegram limits, SQLite path and boot checks. |
| `strategy.yaml` | S1–S5 parameters and consensus configuration. |
| `veto.yaml` | Guard inputs and block/degrade policies. |
| `news_sources.yaml` | Source definitions, credibility, polling and category behavior; placeholder URLs remain unverified. |
| `risk.yaml` | Signal sizing policy, risk/veto cluster inputs, symbol tiers and paper-report assumptions. |

## Paper assumptions and actual sizing

`risk.yaml` includes `paper_trading_assumptions` for reports and operator reference: ₹200,000 capital, $2,400 approximate equivalent at ₹83/USD, ₹5,000 per assumed R, 2.5% of the assumed INR capital, 10x assumed leverage, and `mode: paper`. These values do **not** set exchange leverage and do not place or manage orders.

Do not conflate this report assumption with the existing sizing policy: the top-level `risk_per_trade_pct` remains **0.5%** and is unchanged. Advisory sizing reads `ASSUMED_ACCOUNT_EQUITY_USD` from the environment (set it to `2400` to match the USD capital assumption). The daily report converts manually entered R using ₹5,000/R; that conversion does not prove the advisory size used that risk amount. See `docs/KNOWN_UNCERTAINTIES.md` before interpreting P&L.

## Environment variables

Copy `.env.example` to a local, excluded `.env` file and review every value before starting. The program does not automatically load `.env`; export variables through the operator's chosen process manager or shell.

| Variable | Purpose |
|---|---|
| `ASSUMED_ACCOUNT_EQUITY_USD` | Required finite positive sizing assumption; not read from a Binance account. Example: `2400`. |
| `TELEGRAM_DRY_RUN` | Defaults to `true`; avoids Telegram network delivery while allowing the pipeline to run. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token, only in the local environment; never commit it. |
| `TELEGRAM_CHAT_ID` | Signal and daily-report destination when dry-run is disabled. |
| `TELEGRAM_ERROR_CHAT_ID` | Separate destination for ERROR/CRITICAL notifications; required with a token when `TELEGRAM_DRY_RUN=false`. |
| `LOG_LEVEL`, `LOG_JSON` | Intended log-level/format controls; actual logging setup should be verified in the deployed entry path. |

No Binance trading credentials are supported. The boot guard refuses startup if `BINANCE_API_KEY` or `BINANCE_API_SECRET` is present.

## Binance environment selection

`system.yaml` contains `binance_env`, defaulting to `mainnet`. The opt-in value `testnet` selects public testnet REST and WebSocket hosts and prints the required warning. Testnet is only for pipeline verification; it is not a signal-generation environment. There is no automatic environment switch. Other values fail validation at the client boundary.

## Validation and maintenance

After a config change, run:

```bash
python scripts/validate_config.py
```

The validator checks the six YAML files, fixed paper-assumption keys, environment selection and cross-file constraints. It does not validate external endpoints, credentials beyond the named trading-key guard, market calibration, live risk, or Telegram delivery. Do not change class E/F thresholds to make a validation or test pass.

Manual paper outcomes are recorded using `python scripts/record_outcome.py --signal-id ... --realized-r ...`; daily reports are printed without sending by default with `python scripts/daily_report.py --dry-run`. Both commands use the configured SQLite path unless `--db` is supplied for isolated testing.
