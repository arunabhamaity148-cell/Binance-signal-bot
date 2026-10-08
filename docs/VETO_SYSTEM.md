# Veto and risk system (as built)

`app/risk/consensus.py` computes effective votes and grade assignments; `app/risk/veto_engine.py` coordinates configured vetoes; additional risk components include BTC regime, channels, and risk engine. Backtesting calls shared consensus and veto code. Signal modeling and sizing support reside in `app/signals/` and `app/risk/`.

The validators and tests exercise specified paths, but no live orchestrator invokes them end-to-end in this checkout. Do not interpret a unit or replay test as live protection. Missing/stale inputs should not be considered passing evidence. Configuration is validated via `scripts/validate_config.py`. Class E/F thresholds remain unchanged and uncalibrated.
