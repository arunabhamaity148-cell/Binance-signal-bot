# Architecture (as built)

## Source layout and flow

`app/data/` contains normalization, snapshots, derivatives and public Binance REST/WebSocket components. `app/strategies/registry.py` registers S1–S5. Candidate signals are evaluated from snapshot/news state; `app/risk/` contains consensus, risk, and veto logic; `app/signals/` models generation/lifecycle/expiry/TP-SL. `app/news/` contains collection, parsing, deduplication, credibility, grouping/corroboration, impact, and decay. `app/database/` contains migrations/models/repository. `app/backtest/` replays bars through strategy and risk components. Telegram formatting/queue/sender modules exist.

Configuration loads six YAML files through `app/config.py`; semantic validation is performed by `scripts/validate_config.py`. Static safety checks are in `scripts/scan_*.py`. Tests are in `tests/`.

## Important absent component

This snapshot has no `app/main.py` or `app/bot.py`; no complete boot/main-loop orchestration is shipped. News engine documentation explicitly places scheduling responsibility on a caller, which is absent. Monitoring directory contains only `__init__.py`. Do not infer that components are wired into a live system merely because modules exist.

## Safety boundary

The Binance REST client's fixed allowlist is public market-data GET endpoints. No authenticated trading client is present. Static scanners are defense-in-depth checks, not a formal proof.
