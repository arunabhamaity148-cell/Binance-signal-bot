# Troubleshooting

1. Run `python scripts/validate_config.py`.
2. Confirm `pytest -q` and all scanners pass.
3. Check one bot process with `pgrep -af 'python -m app.main'`.
4. Inspect `ws_stream_health`, `candidate_created`, `diag_veto`, `s3_ls_ratio_refresh`, `derivatives_history_refresh_failed`, and `STARVATION_` events.
5. Distinguish a quiet market from a broken pipeline: healthy feed plus strategy rejection is different from stale feed plus zero candidates.
6. Do not bypass a guard or add credentials to make a signal appear.
