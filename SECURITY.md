# Security policy and boundaries

## Current capability boundary

The live runtime uses public Binance USDⓈ-M REST/WebSocket market-data paths and checks at startup that the configured forbidden exchange trading credentials are absent. The configured list currently includes `BINANCE_API_KEY` and `BINANCE_API_SECRET`. Code and static scans enforce a signal-only boundary: no exchange order placement, cancellation, modification, position close, leverage change, transfer, or withdrawal capability is intended or present in the verified call paths. Signals may be written to SQLite and sent to Telegram; Telegram delivery is separate from exchange execution.

Never add exchange trading credentials, private key material, or order endpoints to this signal-only project. Keep Telegram bot credentials and other secrets out of version control and logs. `.gitignore` excludes `.env` and runtime databases/logs; `.env.example` is the safe template. If a secret is accidentally committed, revoke/rotate it and remove it from history as appropriate.

## Runtime and operational caveats

The Python `app.main` runtime exists but has not been live-validated. Its credential guard checks for forbidden names in the process environment; that is a startup check, not a full secret-management system. Telegram dry-run is the default shown in `.env.example`; disabling it enables external message delivery, not exchange trading. News URLs remain literal placeholders until an operator verifies/replaces them.

`app/monitoring/` utilities are not all wired into the live bot; there is no dedicated error notifier or daily report in this version. Do not infer live alerting, availability, or incident response from standalone scripts or tests.

## Container boundary

The Docker image creates a non-root user and makes its application tree read-only. The default command is config validation only. Compose runs the config validator with `network_mode: none`, read-only filesystem, dropped capabilities, and no-new-privileges. These are validation-only settings; they do not package a network-enabled runtime deployment.

## Verification and reporting

Before accepting changes, review the diff, dependencies, runtime environment, and generated artifacts; run the test suite and all three safety scanners. Scanners are useful checks, not proof against every defect, dependency compromise, or malicious change. No scan can certify profitability, live reliability, or production readiness. Security concerns should be reported privately to maintainers; do not publish exploitable details before coordinated remediation.
