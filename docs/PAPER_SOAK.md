# 72-Hour Paper Soak

The soak monitors the live signal-only process without placing orders. It records WebSocket health, candidates, signals, strategy reasons, vetoes, errors, news health and S3 freshness.

Important events:

- `s3_ls_ratio_refresh` — fetched rows and fetched/merged ages;
- `derivatives_history_refresh_failed` — refresh failure;
- `news_source_disabled` — expected configured disablement;
- `STARVATION_WARNING_ALL_STRATEGIES_REJECTED` — one hour of zero candidates/signals with rejection evidence;
- `STARVATION_CRITICAL_STALE_FEED` — two hours of starvation with stale/unhealthy feed evidence;
- `STARVATION_CRITICAL_ALL_STRATEGIES_REJECTED` — two hours with healthy feeds but continued strategy rejection.

Five-minute quiet periods are normal. Do not call the bot production-signed until the 72-hour run completes with no unexplained critical alert.
