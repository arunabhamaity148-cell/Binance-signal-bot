# Configuration Reference

The runtime loads YAML from `config/`. Validate with `python scripts/validate_config.py`.

- `system.yaml`: public Binance endpoints, timing, database and runtime policy.
- `strategy.yaml`: S1–S5 and consensus.
- `veto.yaml`: exactly G1, G2, G4, G5, G6, G8 and G9.
- `risk.yaml`: advisory sizing, expiry, tiers and paper assumptions.
- `top20_pairs.yaml`: symbol and precision metadata.
- `news_sources.yaml`: optional news feeds and polling.
- `delta.yaml`: public Delta conversion metadata only.

`ASSUMED_ACCOUNT_EQUITY_USD` is an advisory sizing assumption, not an exchange balance. Telegram variables are optional and must stay out of Git. No Binance private trading credentials are supported.
