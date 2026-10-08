# Architecture (as built)

This describes the checked-in implementation, not a future design. The application is a **signal-only** advisory system: it reads public Binance USDⓈ-M market data, computes signals and advisory size, persists signals, and optionally sends Telegram messages. It does not place, cancel, modify, or close exchange orders, set leverage, transfer assets, or withdraw funds.

## Boot sequence

`python -m app.main` invokes `app.main.run_application` through `main()` and `asyncio.run`. Boot has six named stages:

1. **Configuration validation** — load and validate the six YAML files.
2. **Assumed-equity validation** — require finite positive `ASSUMED_ACCOUNT_EQUITY_USD`; this is an operator assumption, not account equity fetched from Binance.
3. **Trading-credential guard** — abort if any credential listed by `config/system.yaml` is present (currently `BINANCE_API_KEY` and `BINANCE_API_SECRET`).
4. **Exchange-info validation** — call public REST `exchangeInfo`; validate each enabled symbol, trading status, and quantity/price filters. Report rejected symbols and require the configured minimum (15 of 20); do not substitute symbols.
5. **WebSocket first-snapshot readiness** — start public streams, REST-backfill per-symbol state, and wait at most 90 seconds for the configured minimum number of complete snapshots. The cache requires 5m history, depth, book ticker, derivative state, and fresh current OI for readiness.
6. **News and evaluation** — start the scheduled news collector and symbol evaluation loop.

Boot failures are logged with their stage and cleanup runs in `finally`. SIGINT/SIGTERM set the runtime stop event when the event loop supports signal handlers.

## Live data and cache

`app.bot.LiveSnapshotCache` combines REST backfills and public WebSocket updates. Streams include klines for 5m/15m/1h/4h/1d, aggregate trades, depth, book ticker, and mark price. The in-memory cache normalizes bars and joins order-book state, recent taker flow, derivatives, and feed health into `MarketSnapshot` objects. A resync callback requests another REST backfill.

There are **two distinct OI update paths**:

- Current OI is polled per symbol approximately every 30 seconds and merged into the live 5m OI series.
- Historical derivatives refresh runs every 15 minutes. Per symbol it requests the prior 24 hours for funding, OI at 5m/15m/1h/1d, global-account long/short ratio, and taker long/short ratio: seven REST calls per symbol. New observations merge by event timestamp; older history is retained for percentile calculations. A failed endpoint is logged and its existing series remains for a later retry.

The scheduled refresh caches the **two strategy-consumed ratio streams** (global account and taker). Other long/short endpoints exposed by the REST client are not part of the `DerivativesState` cache or this refresh path.

## Evaluation, persistence, delivery

For each ready symbol, `SignalBot` builds a snapshot and news state, runs the strategy registry, and records candidate audits. It grades candidates through consensus, invokes `run_veto_engine`, applies any grade cap, checks repository-backed risk state and risk limits, calculates advisory sizing, constructs a final signal, and checks configured TP2 R:R. Passing signals are inserted into SQLite as `PENDING`, placed on an in-memory outbox, and handled by a publisher task. A successful sender result advances lifecycle to `PUBLISHED`; refusal or delivery failure leaves it `PENDING` and is logged. Telegram dry-run is the default in `.env.example`; disabling it performs external Telegram delivery but still does not create exchange orders.

The veto engine catches guard exceptions as BLOCKs. The live bot currently supplies `funding_z=None` and `btc_trend_direction=None` to its veto call, so guards requiring those optional context values do not receive computed values from this path.

## Monitoring boundary

`app/monitoring/` contains health-reporting, metrics, journal, and soak-observer utilities. They are **not all wired into the live bot**. The live bot uses its SQLite repository and normal logging; standalone healthcheck, smoke, soak, and canary scripts have separate bounded purposes. There is no shipped daily-report job or dedicated Telegram error notifier in this version. A standalone check or synthetic smoke run cannot establish live runtime health.

## Limits

- No private exchange API client or exchange trading endpoint is used by the bot.
- The live runtime's network operation has not been verified against Binance or Telegram in this project work.
- No paper execution simulator runs inside `app.main`; backtest replay and synthetic smoke/canary tools are separate offline paths.
- Passing tests establishes only tested behavior, not calibration, profitability, operational reliability, or production readiness.
