# Repository structure (current checkout)

This tree is reconciled against the working checkout after Additions A–E and lists 186 source, configuration, documentation, script, and test files. It excludes `.git`, ignored caches, local secrets, runtime databases/logs, and generated artifacts. The obsolete `FINAL_RELEASE_REPORT.md` and `binance_signal_bot_PRODUCTION_FINAL.zip` remain absent because they represented an incomplete pre-5B–5D state. The current phase explicitly defers final packaging. `README.md` is now the requested Bengali operator guide.

```text
.
├── .dockerignore
├── .env.example
├── .gitignore
├── CONFIG_REFERENCE.md
├── docker-compose.yml
├── Dockerfile
├── LICENSE
├── PRODUCTION_RUNBOOK.md
├── pyproject.toml
├── README.md
├── REPO_STRUCTURE.md
├── requirements.txt
├── SECURITY.md
├── app
│   ├── __init__.py
│   ├── bot.py
│   ├── config.py
│   ├── main.py
│   ├── safety.py
│   ├── backtest
│   │   ├── __init__.py
│   │   ├── costs.py
│   │   ├── engine.py
│   │   ├── fills.py
│   │   ├── harness.py
│   │   ├── metrics.py
│   │   └── walk_forward.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   ├── logging.py
│   │   ├── math.py
│   │   ├── models.py
│   │   └── time_utils.py
│   ├── data
│   │   ├── __init__.py
│   │   ├── derivatives.py
│   │   ├── normalization.py
│   │   ├── orderbook.py
│   │   ├── snapshot.py
│   │   └── binance
│   │       ├── __init__.py
│   │       ├── health.py
│   │       ├── models.py
│   │       ├── rest.py
│   │       └── websocket.py
│   ├── database
│   │   ├── __init__.py
│   │   ├── migrations.py
│   │   ├── models.py
│   │   └── repository.py
│   ├── monitoring
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── journals.py
│   │   ├── metrics.py
│   │   └── soak.py
│   ├── news
│   │   ├── __init__.py
│   │   ├── collectors.py
│   │   ├── correlation.py
│   │   ├── credibility.py
│   │   ├── decay.py
│   │   ├── deduper.py
│   │   ├── engine.py
│   │   ├── impact.py
│   │   └── parser.py
│   ├── risk
│   │   ├── __init__.py
│   │   ├── btc_regime.py
│   │   ├── channels.py
│   │   ├── consensus.py
│   │   ├── risk_engine.py
│   │   ├── veto.py
│   │   └── veto_engine.py
│   ├── signals
│   │   ├── __init__.py
│   │   ├── danger.py
│   │   ├── expiry.py
│   │   ├── lifecycle.py
│   │   ├── models.py
│   │   ├── signal_engine.py
│   │   └── tpsl.py
│   ├── strategies
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── s1_liquidity_sweep.py
│   │   ├── s2_volatility_compression.py
│   │   ├── s3_funding_crowding.py
│   │   ├── s4_oi_trend.py
│   │   └── s5_oi_regime.py
│   └── telegram
│       ├── __init__.py
│       ├── error_notifier.py
│       ├── formatter.py
│       ├── queue.py
│       ├── reports.py
│       └── sender.py
├── config
│   ├── news_sources.yaml
│   ├── risk.yaml
│   ├── strategy.yaml
│   ├── system.yaml
│   ├── top20_pairs.yaml
│   └── veto.yaml
├── docs
│   ├── ARCHITECTURE.md
│   ├── BACKTESTING.md
│   ├── DEPLOYMENT.md
│   ├── KNOWN_UNCERTAINTIES.md
│   ├── NEWS_ENGINE.md
│   ├── STRATEGIES.md
│   ├── TROUBLESHOOTING.md
│   └── VETO_SYSTEM.md
├── scripts
│   ├── _offline_pipeline.py
│   ├── canary.py
│   ├── daily_report.py
│   ├── healthcheck.py
│   ├── record_outcome.py
│   ├── run_backtest.py
│   ├── run_backtest_jsonl.py
│   ├── run_walkforward.py
│   ├── scan_forbidden_calls.py
│   ├── scan_no_placeholders.py
│   ├── scan_signal_only.py
│   ├── smoke_test.py
│   ├── soak_test.py
│   └── validate_config.py
└── tests
    ├── __init__.py
    ├── conftest.py
    ├── integration
    │   ├── test_backtest_engine_e2e.py
    │   ├── test_backtest_fill_probability.py
    │   ├── test_backtest_g2_unhealthy_blocks.py
    │   ├── test_backtest_jsonl_derivatives.py
    │   ├── test_backtest_latency_applied.py
    │   ├── test_backtest_lookahead.py
    │   ├── test_backtest_no_csv.py
    │   ├── test_backtest_s4_jsonl_fixture.py
    │   ├── test_canary_limited_scope.py
    │   ├── test_daily_report_cli.py
    │   ├── test_derivatives_periodic_refresh.py
    │   ├── test_main_boot_sequence.py
    │   ├── test_monitoring_scripts.py
    │   ├── test_pipeline_e2e.py
    │   ├── test_record_outcome_cli.py
    │   ├── test_report_event_wiring.py
    │   ├── test_signal_pipeline_live_path.py
    │   ├── test_soak_pending_before_72h.py
    │   ├── test_telegram_format.py
    │   └── test_walkforward_holdout_protection.py
    ├── news
    │   ├── test_collectors.py
    │   ├── test_correlation.py
    │   ├── test_credibility.py
    │   ├── test_decay.py
    │   ├── test_deduper.py
    │   ├── test_engine.py
    │   ├── test_impact.py
    │   └── test_parser.py
    ├── risk
    │   ├── test_channels_btc_veto_engine.py
    │   ├── test_consensus.py
    │   └── test_risk_engine.py
    ├── safety
    │   ├── test_scan_forbidden_calls.py
    │   ├── test_scan_no_placeholders.py
    │   └── test_scan_signal_only.py
    ├── signals
    │   ├── test_advisory_warning_presence.py
    │   ├── test_costs.py
    │   ├── test_danger.py
    │   ├── test_expiry.py
    │   ├── test_lifecycle.py
    │   ├── test_signal_engine_registry.py
    │   └── test_signal_models.py
    ├── strategies
    │   ├── test_min_stop_cost_filter.py
    │   ├── test_registry.py
    │   ├── test_s1_liquidity_sweep.py
    │   ├── test_s2_volatility_compression.py
    │   ├── test_s3_funding_crowding.py
    │   ├── test_s4_oi_trend.py
    │   └── test_s5_oi_regime.py
    ├── unit
    │   ├── test_backtest_metrics.py
    │   ├── test_binance_environment.py
    │   ├── test_binance_models.py
    │   ├── test_config.py
    │   ├── test_core_math.py
    │   ├── test_daily_report.py
    │   ├── test_derivatives.py
    │   ├── test_error_notifier.py
    │   ├── test_fills.py
    │   ├── test_harness.py
    │   ├── test_migrations.py
    │   ├── test_monitoring_modules.py
    │   ├── test_normalization.py
    │   ├── test_orderbook.py
    │   ├── test_repository.py
    │   ├── test_run_backtest_jsonl_no_input.py
    │   ├── test_safety.py
    │   ├── test_snapshot.py
    │   ├── test_time_utils.py
    │   └── test_walk_forward.py
    └── veto
        └── test_veto_guards.py
```
