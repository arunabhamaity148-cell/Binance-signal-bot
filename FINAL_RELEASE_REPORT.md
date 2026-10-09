# Final Release Report — Binance Signal-Only Futures Bot

## Release identity

- Branch: `main`
- Latest local commit: `219647c`
- Artifact: `binance_signal_bot_PRODUCTION_FINAL.zip`
- Scope: signal-only Binance USDⓈ-M market-data advisory bot; manual Delta execution only

## Verification results

| Check | Result |
|---|---:|
| `pytest -q` | **PASS — 940 passed** |
| `python scripts/validate_config.py` | **PASS** |
| `python scripts/scan_signal_only.py` | **PASS** |
| `python scripts/scan_no_placeholders.py` | **PASS** |
| `python scripts/scan_forbidden_calls.py` | **PASS** |
| `git diff --check` | **PASS** |
| Active production vetoes | **PASS — exactly 7** |

## Active veto stack

Enabled: **G1, G2, G4, G5, G6, G8, G9**.

Disabled but retained in source for reference/shadow mode: **G3, G7, G10, G11, G12, G13, G14, G15, G16**.

## Included fixes

The release contains the verified fixes covering Delta non-fatal handling, WebSocket subscriptions/freshness, S1 confidence/reclaim/diagnostics, regime ATR and ADX handling, zero-confidence protection, S2/S4 dispatch, candidate finalization visibility, veto exception safety, news redirects/enabled flags/dead-source handling, S3 freshness and extreme-funding verification, continuous S1 factors, full G3 depth20 support, G16 4H structure detection, seven-guard production configuration, G16 repository whitelist support, G16 SQLite CHECK support, and seven-guard Telegram operator messaging.

## Artifact exclusions

The source ZIP excludes `.git`, virtual environments, Python caches, pytest caches, `.env` secrets, runtime logs, and local databases. `.env.example` is included as a configuration template.

## Operational limitations

The offline test suite and static scanners do not establish profitability, live feed quality over a long soak, Telegram delivery reliability, out-of-sample performance, or execution outcomes. The bot remains advisory and signal-only; no order-placement credentials or trading endpoints are used.
