# Repository Structure

`app/` contains runtime code: public Binance data, S1–S5 strategies, consensus, the seven-guard veto engine, advisory risk, signals, Telegram, news, monitoring, persistence and public Delta conversion.

`config/` contains YAML policy. `scripts/` contains validation, backtest, health and soak tools. `deploy/` contains service units. `docs/` contains operator and design references. `tests/` contains all unit/integration/safety tests, including retained retirement regressions for removed guard names.

The cleaned tree intentionally contains no disabled-veto implementation, no order-placement client, and no stale generated production ZIP.
