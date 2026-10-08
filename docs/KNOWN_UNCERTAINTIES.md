# Known uncertainties (current checkout)

This is an as-built record of what remains unvalidated. Unit and integration tests use deterministic fixtures; passing them does not establish market calibration, live reliability, or profitability. Values are not adjusted by this documentation update.

## 1. Class E/F thresholds and strategy parameters

Many values in `config/strategy.yaml`, `config/veto.yaml`, `config/risk.yaml`, `config/system.yaml`, and `config/news_sources.yaml` are explicitly class E/F engineering assumptions. They have not been calibrated against representative Binance USDⓈ-M history for the configured 20-symbol universe. Original design-spec files referred to by early notes are not part of this checkout; the shipped YAML and code are the source of truth for current behavior.

## 2. Consensus grade boundaries

The grade boundaries, vote weights, confidence cutoffs, and effective-vote formula in the shipped strategy config have deterministic tests, but have not been shown to separate higher-quality from lower-quality setups on out-of-sample or live observations.

## 3. Depth, spread, and execution-quality thresholds

Tier-specific depth/spread thresholds and execution-quality decisions are configured in the shipped risk/veto files. Order-book depth and spread vary by symbol, session, and market regime. No representative exchange sample has been used to validate that the tiers reliably distinguish executable from unsafe conditions.

## 4. News source URLs and access terms

`config/news_sources.yaml` still contains literal placeholder strings, including `"<official Fed RSS URL>"`, `"<CoinDesk RSS URL>"`, and `"<GDELT public API endpoint>"`. These are not verified feeds. The same config has provisional source names/formats and category half-lives. Operators must verify actual endpoints, terms, format, and free-tier/rate limits before relying on this subsystem. No replacement URLs are inferred here.

## 5. News impact decay and classification

The configured category half-lives, credibility weights, parsing, entity mapping, corroboration, and impact/severity classification are implementation choices. They have not been evaluated against measured market reaction and may misclassify or miss material events.

## 6. Slippage and latency-cost calibration

A concrete model is implemented, replacing the earlier “choice not fixed” note. `estimate_slippage_bps` is linear in `notional_usd / depth_usd`, at 1 bp per 10% of depth consumed, capped at 50 bps; missing/non-positive depth falls back to 5 bps. Backtest latency slippage is linear at 0.5 bps/second, capped at 25 bps, and the engine's default assumed latency is 2 seconds. These values are not calibrated from observed executions or order-book replays. Live signal costing uses the current-snapshot model and does not charge the backtest-only latency assumption.

## 7. Swing/structure detection

Swing highs/lows are implemented in `app/core/math.py` as symmetric fixed-lookback pivots: a bar is a pivot when it is the high/low extreme across `lookback` bars on both sides. S1, S3, and S4 use this shared definition (with their current lookback settings). This is concrete in code, but the pivot convention's robustness, lag, tie handling, and predictive usefulness have not been validated out of sample.

## 8. Correlated-cluster coverage

The configured explicit cluster currently covers BTC/ETH/SOL (`majors_beta`). Other configured pairs are not grouped unless listed; correlation regimes among altcoins have not been measured for this risk cap.

## 9. REST request load and OI refresh paths

Current OI and historical series are distinct:

- The live current-OI endpoint is polled every 30 seconds per symbol (about 40 raw requests/minute if all 20 symbols are active). The module's stated `/fapi/v1/openInterest` weight of one gives an estimated 40 weight/minute for this path alone, about 1.7% of the configured 2400 weight/minute budget.
- The 15-minute historical refresh requests funding, OI history at 5m/15m/1h/1d, global-account long/short ratio, and taker long/short ratio: 7 calls per symbol, or 140 raw calls per cycle for 20 symbols (about 9.3 raw calls/minute averaged over 15 minutes). Older history is retained when 24-hour-window responses are merged.
- Startup performs REST backfill, and WebSocket resync can request it again.

The official Binance USDⓈ-M market-data documentation (checked 2026-10-08) lists IP Weight 0 for `/futures/data/openInterestHist`, `/futures/data/globalLongShortAccountRatio`, and `/futures/data/takerlongshortRatio`, with a separate 1000-requests/5-minute IP limit for these data endpoints. For `/fapi/v1/fundingRate`, the documentation states a shared 500-requests/5-minute/IP cap with `/fapi/v1/fundingInfo` but gives no numeric IP weight. Consequently, the documented recurring weighted subtotal is **40 weight/minute** from current OI; the six zero-weight history calls add no documented IP weight, while funding contributes `(20/15) × its undocumented per-call weight` per minute. The exact aggregate weight cannot be computed from the published values. The known subtotal is `40/2400 = 1.6667%` of the configured budget; that is **not** a total-utilization figure and is insufficient to classify the complete load as comfortable. Do not convert the 140-call count into a weight or percentage.

Reserved but unused: `/futures/data/takerlongshortRatio` history is refreshed every 15 minutes but not consumed by any current strategy. Retained for future cross-check capability. If no strategy consumes it after 6 months of operation, remove it.

Other recurring Binance REST paths are absent in the inspected runtime loops: evaluation uses WebSocket/cache data and news polling uses external sources. WebSocket resync can trigger a non-periodic full REST backfill. At cold boot, the current 20-symbol configuration makes 100 kline calls (`limit=100`, weight 2 each), 20 depth calls at limit 20 (weight 2), 20 book-ticker calls (weight 2), 20 aggTrades calls at limit 500 (weight 20), 20 current-OI calls (weight 1), and one exchangeInfo call (weight 1): **701 documented weight once**, plus 20 funding-rate calls whose numeric weight is not published. The six `/futures/data/*` calls per symbol have published weight zero. This boot/resync burst is not per-minute recurring load. The `app/data/derivatives.py` module comment still refers to 5-minute historical gap-fill; the active `app.bot.SignalBot` history loop is 15 minutes and is authoritative for this checkout.

## 10. Backtest realism and out-of-sample evidence

The JSONL harness can replay bars with order-book, taker-flow, derivative, and higher-timeframe context; CSV runners carry OHLCV only. The current backtest includes latency cost and seeded bar-level limit-fill probability, but neither can model queue position, true order matching, partial execution, intrabar path, network variation, or human reaction. CSV/JSONL outputs are not live fill results. The walk-forward runner keeps shipped config fixed and reports holdout without fitting; this is not evidence of strategy profitability or a broad independent OOS study.

## 11. Fixture observations for S1/S2/S5 cost gates

These are **fixture observations or formula estimates**, not numeric acceptance assertions that guarantee the same values across live data:

- **S1:** The current `test_pipeline_e2e.py` realistic fixture recomputes to post-cost TP2 R:R **1.731179** against configured `min_rr_tp2 = 1.8`; the fixture test asserts the gate result agrees with calculated R:R, not the exact 1.731179 value. Earlier notes' $202 ATR / 20 bps and 1.693 R:R describe a superseded fixture. The current fixture's ATR(14) is approximately $314 at a price near $100,084 (about 31.3 bps); exact TP geometry is covered separately by strategy tests.
- **S2:** The current pre-breakout fixture used by `test_s2_volatility_compression.py` measures ATR(14) about $1,432 at $99,700 (about 143.6 bps). Its test verifies the candidate clears the configured 3x stop-cost filter; it does not assert an exact minimum ATR boundary. The module's older approximate 136 bps / 20 bps comparison is not the current S1/S2 fixture pair.
- **S5:** The current $100,000-scale fixture measures ATR(14) about $2,306 (about 230.6 bps) and its test verifies that the candidate clears the configured TP2 R:R gate. The roughly 93 bps figure is a formula-derived approximate boundary for 0.6 ATR stop distance, two 2-bps maker fees, and a 1.8 R:R floor; the suite does not binary-search or assert that boundary.

These observations highlight interactions between ATR-based stop geometry, fees, and cost filters; they do not establish how often any strategy will pass on real data. Do not tune class E/F parameters to make synthetic fixtures pass or to match these observations. Real distribution evidence by symbol and regime is still required.

## 12. Paper report assumptions and manual outcomes

The report's assumed INR conversion is ₹5,000 per R, but the existing top-level `risk_per_trade_pct` remains 0.5%; advisory sizing uses `ASSUMED_ACCOUNT_EQUITY_USD`. The new descriptive paper block separately states 2.5% of ₹200,000. These figures are not reconciled by the software. The daily report multiplies manually recorded `realized_r` by ₹5,000 and must not be interpreted as actual account P&L, exchange-reconciled P&L, or evidence that advisory sizing used ₹5,000 risk per R. Outcomes are operator-entered, one per signal, and are not inferred from fills.

## 13. Daily report and error-notifier operation

The daily report is scheduled for 23:59 in `Asia/Kolkata` by a process-local asyncio task. It uses SQLite rows and is not independently persisted as a scheduled-job record; missed schedules during downtime are not replayed. Live timing, restart recovery, and Telegram delivery have not been verified. Error notification throttling is also process-local (one notification attempt per five minutes) and resets on restart. A bounded 512-record queue avoids blocking the logging caller; overflow is written to stderr and may mean an event was not persisted. The notifier starts after config, assumed-equity, and trading-credential checks, so failures before that point are outside its event capture window. Database/Telegram failure handling is fail-soft, not a durable alert guarantee.

## 14. Testnet data suitability

`binance_env: testnet` switches public market-data URLs and produces an explicit warning. Testnet market data can be sparse and does not validate mainnet liquidity, feed reliability, strategy quality, or real-world fills. Testnet output must not be used as live trading signals. No external testnet session has been verified here.

---

The current evidence is bounded: source inspection, deterministic tests, configuration checks, and static scans. Live Binance/news/Telegram validation, paper-soak evidence, robust OOS performance, threshold calibration, and profitability are **NOT TESTED / NOT VERIFIED**. No broader claim is implied by test success.
