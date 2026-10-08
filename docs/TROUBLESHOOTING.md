# Troubleshooting

- **Config validation fails:** run `python scripts/validate_config.py`, inspect all six YAML mappings and reported field paths; do not bypass the validator.
- **Strategy returns no candidate:** verify the snapshot contains the strategy's required taker-flow/derivatives data, symbol metadata, sufficient candles, and valid news state. Empty results are not proof of a defect or market condition.
- **Backtest produces zero trades:** inspect auxiliary input availability, time alignment, costs, veto outcomes, and CSV schema. Do not fabricate missing series.
- **News output is empty/degraded:** verify configured URLs, provider terms, parsing compatibility, source health and timestamps. No feed's availability is guaranteed.
- **Test import/dependency errors:** install pinned `requirements.txt` in a fresh Python 3.11 environment. Preserve failures; do not skip/xfail tests.
- **No `healthcheck.py` or smoke test:** these scripts are absent in the inspected checkout; no equivalent operational check is claimed.
