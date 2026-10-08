# Repository structure (verified checkout)

The source snapshot on branch `main` at baseline commit `2e36bf1` had 134 tracked paths before Batch 6; current files below document the baseline and Batch 6 additions; the reproducible package contains 151 files, including tests. `docs/KNOWN_UNCERTAINTIES.md` is the canonical uncertainties record. The supplied handoff claimed 134 files from a Batch 5A archive, but that archive was not available and its digest was not independently verified.

```text
.
├── .dockerignore
├── .env.example
├── .gitignore
├── CONFIG_REFERENCE.md
├── Dockerfile
├── FINAL_RELEASE_REPORT.md
├── LICENSE
├── PRODUCTION_RUNBOOK.md
├── README.md
├── REPO_STRUCTURE.md
├── SECURITY.md
├── docker-compose.yml
├── app/                 # backtest, core, data, database, monitoring*, news, risk, signals, strategies, telegram
├── config/              # six YAML configs
├── docs/                # as-built module docs and KNOWN_UNCERTAINTIES.md
├── scripts/             # backtest/walkforward, config validation, three scanners
├── tests/               # pytest suite
├── pyproject.toml
└── requirements.txt
```

`*` `app/monitoring/` currently contains only `__init__.py`. Missing requested/planned files include `app/main.py`, `app/bot.py`, monitoring health/metrics/journals/soak modules, `scripts/run_backtest_jsonl.py`, `smoke_test.py`, `healthcheck.py`, `soak_test.py`, `canary.py`, and `REPO_STRUCTURE.md` at baseline. These are not silently represented as shipped. No Phase-1 design files were in the verified repository; completeness against unspecified Phase-1 deliverables is NOT VERIFIED.
