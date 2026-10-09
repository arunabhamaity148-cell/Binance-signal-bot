# Production Runbook

This is a signal-only advisory service. Manual Delta execution is outside the repository.

## Deploy and verify

```bash
cd ~/Binance-signal-bot
source venv/bin/activate
git pull --ff-only origin main
python scripts/validate_config.py
pytest -q
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

Start one process with `python -m app.main`; do not run duplicate processes. Review the log for `candidate_created`, `signal`, `diag_veto`, `s3_ls_ratio_refresh`, `derivatives_history_refresh_failed`, `news_source_disabled`, and `STARVATION_` events.

A deployment is not production-signed until the 72-hour paper soak completes without unexplained critical alerts. Roll back with a known-good commit and rerun all validation before restart.
