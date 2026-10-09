# Known Uncertainties

- No code test proves profitability or future market performance.
- Public Binance feeds can disconnect, lag, change schema or rate-limit.
- News feeds are delayed and optional; disabled feeds are intentionally skipped.
- S3 long/short freshness must be evaluated from live `s3_ls_ratio_refresh` evidence during the soak.
- Advisory sizing does not observe account balance, margin, fills or liquidation distance.
- Manual Delta execution introduces venue, latency and operator differences.
