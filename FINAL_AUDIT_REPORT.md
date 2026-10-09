# Binance Signal-Only Futures Bot — Final Audit

**Audit date:** 2026-10-10  
**Release branch:** `main`  
**Scope:** Verify the listed bug fixes, enforce exactly seven active production vetoes, and prepare a clean source ZIP.

## Validation result

| Check | Result |
|---|---:|
| Pytest | **940 passed** |
| Config validation | **PASS** |
| Signal-only scanner | **PASS** |
| No-placeholder scanner | **PASS** |
| Forbidden-calls scanner | **PASS** |
| `git diff --check` | **PASS** |
| Active production vetoes | **7** |

## Active production vetoes

Only these guards are enabled:

- G1 — Data Integrity
- G2 — Feed Health
- G4 — Spread Explosion
- G5 — OI Anomaly
- G6 — Funding Extreme
- G8 — Volatility Flash
- G9 — BTC Regime

Disabled guards retained in code for future reference/shadow mode:

- G3 — Depth Collapse
- G7 — News Shock
- G10 — Orderbook Instability
- G11 — Execution Quality
- G12 — Self-Consistency
- G13 — OI Divergence
- G14 — OI Stagnation
- G15 — OI Percentile Extreme
- G16 — Multi-TF Confluence

## Fix coverage

1. Delta failure is non-fatal — covered by Delta fetch-failure integration tests.
2. WebSocket subscriptions and freshness — covered by WebSocket and snapshot-health tests.
3. S1 confidence formula — covered by S1 confidence regression tests.
4. G6/G9 wiring — covered by veto-engine channel/order tests.
5. Regime ATR percentile — covered by ATR-percentile integration tests.
6. Regime ADX tiers — covered by ADX classifier tests.
7. Zero-confidence emission — covered by zero-confidence strategy tests.
8. S2 in RANGING — covered by the S2 ranging regression.
9. S1 reclaim sign — covered by reclaim-factor tests.
10. S1 reclaim threshold — covered by threshold and cross-symbol tests.
11. Silent candidate finalization — covered by candidate-pipeline visibility tests.
12. Veto-block crash handling — covered by veto-engine exception handling tests.
13. Diagnostic reasons — covered by diagnostic reason tests.
14. S4 dispatcher wiring — covered by S4 dispatcher integration tests.
15. S1 diagnostics — covered by S1 diagnostic wiring tests.
16. News URLs and redirects — covered by news collector redirect tests and validated source configuration.
17. S3 funding freshness threshold — covered by S3 freshness/prerequisite tests.
18. S3 extreme funding verification — covered by the historical extreme-funding test.
19. S1 continuous quality factors — covered by smooth-factor, partial-quality, and confidence tests.
20. G16 Python guard-name whitelist — fixed so repository persistence accepts G16 for shadow-mode testing.
21. News source enabled flag — implemented and covered by collector/dead-source tests.
22. G3 full depth20 window — configured and covered by full-book integration tests.
23. G16 SQLite CHECK whitelist — fixed so runtime-event persistence accepts G16.
24. G16 4H structure window — configured to 40 bars and covered by structure-detection tests.

## Release notes

- The bot remains **signal-only** and does not place, cancel, modify, or close trades.
- Delta is advisory conversion only; operator execution remains manual.
- The ZIP is source-only and excludes `.git`, virtual environments, caches, logs, databases, and environment files.
- Live Binance/Telegram operation, paper soak, out-of-sample performance, and profitability are operational validations rather than claims established by this offline release audit.
