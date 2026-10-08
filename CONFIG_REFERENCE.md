# Configuration reference

The loader requires six top-level YAML mappings under `config/`: `top20_pairs.yaml`, `system.yaml`, `strategy.yaml`, `veto.yaml`, `news_sources.yaml`, and `risk.yaml`. `app/config.py` loads them; `scripts/validate_config.py` performs semantic checks. Run that validator after edits.

| File | Observed responsibility |
|---|---|
| `top20_pairs.yaml` | Pair universe and exchange precision/fee metadata |
| `system.yaml` | Runtime/data-system and API budget settings |
| `strategy.yaml` | Strategy parameters and consensus |
| `veto.yaml` | Guard thresholds and veto configuration |
| `news_sources.yaml` | Source definitions, credibility and category decay |
| `risk.yaml` | Sizing/risk settings, tiers and correlated clusters |

Exact keys and constraints are enforced by the validator and are best consulted in the YAML plus `scripts/validate_config.py`; this reference intentionally does not duplicate the full schema. `ASSUMED_ACCOUNT_EQUITY_USD` is read from environment and must be numeric and greater than zero. It is an assumed input, not account data. No exchange API credentials should be provided. Keep local secrets in excluded `.env` files and never commit them.

Threshold calibration and feed/source availability are uncertain; see `docs/KNOWN_UNCERTAINTIES.md`. Do not tune class E/F thresholds as part of packaging.
