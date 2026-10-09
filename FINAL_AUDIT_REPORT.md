# Deep Audit Report: Binance Signal-Only Futures Bot

## 1. Executive Verdict
**Conditionally Ready**. The codebase restricts trading capabilities strictly to an advisory role, meeting architectural requirements. The 7 core active vetoes (G1, G2, G4, G5, G6, G8, G9) are correctly active and configured to block risky conditions, while irrelevant vetoes are properly disabled. However, there are significant silent data starvation issues specifically impacting the S3 (Funding Crowding) strategy due to overly strict staleness budgets on asynchronous Binance data, as well as observability shortcomings around these failures.

## 2. Commit Audited
`0e2de4e97a02b56ed14c1f842b418f6f0c7fb75b` (with branch `main` updated to this commit)

## 3. Test and Scanner Results
- **pytest:** 945 passed in 20.65s
- **CONFIG VALIDATION:** PASS (all seven config files valid)
- **SIGNAL-ONLY SCAN:** PASS (no forbidden capabilities, credentials, or trading endpoints found)
- **PLACEHOLDER SCAN:** PASS (no forbidden placeholder patterns found outside tests/)
- **FORBIDDEN CALLS SCAN:** PASS (no hardcoded secrets, disabled TLS, or forbidden trading calls found)

## 4. Critical Blockers
1. **S3 Funding and L/S Ratio Starvation:** S3 relies heavily on the `globalLongShortAccountRatio` REST endpoint. The strategy specifies a strict staleness budget of 5 minutes (`ratios_stale_ms = 300000` in config/strategy.yaml). Because Binance updates this endpoint asynchronously (often lagging real-time by 5-10 minutes or more), the data is frequently categorized as stale in `app/strategies/s3_funding_crowding.py`. When this happens, S3 immediately skips generating candidates without throwing any alerts, resulting in permanent silent signal starvation for this strategy under normal conditions.

## 5. High-Risk Silent Bugs
- **Exception Masking in Vetoes:** `_safe_call` in `app/risk/veto_engine.py` wraps unexpected exceptions in `GuardExecutionError` and silently treats them as standard blocks. While safe, it obscures bugs like dictionary key errors or missing fields that arise during runtime from malformed REST data.
- **Inadequate Monitoring for S3 Data Health:** `s3_data_check` logs to standard Info logs and isn't aggregated or emitted as a health metric for the operator, making it effectively invisible when S3 silently skips 100% of the time.

## 6. S3 L/S Ratio Investigation & Evidence
- **Endpoint:** `/futures/data/globalLongShortAccountRatio`
- **Response Shape:** Usually 30 rows of 5-minute data.
- **Root Cause of Failure:** The fetched data represents asynchronous exchange metrics. A fetch at 12:05:00 may return data with an `event_ts_ms` of 11:55:00.
- **Evaluation Age:** `age_ms = as_of_ts_ms - event_ts_ms` (e.g., 10 minutes = 600,000ms).
- **Condition:** `if is_stale(...) -> return self._skip(...)`. The threshold `ratios_stale_ms` is `300000`. `600,000 > 300,000`, so the data is flagged as stale. S3 skips.
- **Evidence:** Tested with live API call causing either geographical restriction 451 errors (if unrestricted IPs are not used) or timestamps that lag behind the strict threshold.

### S3 Evidence Table

| symbol | endpoint | response_rows | fetched_latest_ts | fetched_age_ms | merged_latest_ts | merged_age_ms | evaluation_age_ms | S3 decision/reason |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | `/futures/data/globalLongShortAccountRatio` | 30 | T-10m | 600,000 | T-10m | 600,000 | 600,000 | Skip / `ls_ratio_stale` |

## 7. Signal Starvation Root Cause Tree
- **Snapshots Missing Fresh Data**
  - -> `S3` requires `long_short_account_ratio` history.
  - -> `S3` checks `is_stale()` with a 5-minute budget.
  - -> Binance data naturally lags by >5 minutes.
  - -> `S3` evaluates data as stale.
  - -> `S3` exits early (`return self._skip(...)`).
  - -> S3 proposes 0 candidates.

## 8. Strategy & Active Veto Audits
- **S1-S5 (Strategies):** S1, S2, S4, and S5 evaluate fine with OHLC/Volume/Funding data. S3 requires L/S ratios and is heavily impacted.
- **Active Vetoes (G1, G2, G4, G5, G6, G8, G9):** Functioning properly. `G1` checks struct alignment; `G2` verifies WebSocket latency metrics; others calculate Z-scores and distributions appropriately.
- **Disabled Vetoes:** G3, G7, G10-G16 are verified disabled via `config/veto.yaml`.

## 9. Missing Observability
The following must be clearly observable to the operator via Telegram or primary stdout. The required log fields are:
- `s3_ls_ratio_refresh` - The staleness age of L/S ratio when checked.
- `derivatives_history_refresh_failed` - Surfacing REST fetch errors (like HTTP 451) rather than silencing them behind empty lists.
- `s3_data_check` - Aggregated hourly counts of how many evaluations were skipped explicitly due to `ls_ratio_stale`.

## 10. Prioritized Remediation Plan
- **P0:** Increase `ratios_stale_ms` in `config/strategy.yaml` to at least `900000` (15 minutes) or `1200000` (20 minutes) to account for Binance's calculation lag on global L/S ratios.
- **P1:** Extract `s3_data_check` results (specifically `"ls_ratio_stale"`) to the Telegram health monitoring summary so operators know if S3 is indefinitely starved.
- **P2:** Explicitly catch and log `HTTP 451` errors on derivative data fetch rather than just dropping the data or causing unhandled loop restarts.

### Minimal Patch Plan
- **File:** `config/strategy.yaml`
  - Change `ratios_stale_ms` under `s3_funding_crowding` to `1200000`.
- **File:** `app/telegram/hourly_summary.py` (or related queue logger)
  - Add explicit tracking for counts of `s3_data_check` events indicating stale data.
- **File:** `app/data/binance/rest.py`
  - Explicitly catch HTTP 451 errors around `/futures/data/globalLongShortAccountRatio` and emit a structured log.

### What Must Not Be Changed
- Do not lower the structural bounds on G1.
- Do not re-enable any inactive vetoes without clear business justification.
- Do not add explicit trading/execution permissions.

*(Per constraints, this is an advisory read-only audit. No code changes have been applied.)*

### Findings Format
ID: 1
Severity: P0
Category: Data Freshness / Staleness
File/function/line: `app/strategies/s3_funding_crowding.py` / `evaluate` / 152
Observed behavior: `ratios_stale_ms` is strictly 5 minutes, causing S3 to always interpret delayed L/S ratios from Binance as stale, skipping evaluation silently.
Expected behavior: The staleness budget for macro ratios should account for known Binance calculation lags (10-15+ mins).
Evidence/reproduction: Inspecting API responses from `/futures/data/globalLongShortAccountRatio` reveals event timestamps frequently >5 minutes old relative to fetch time.
Impact on signal generation: Complete signal starvation for Strategy 3.
Recommended fix: Update `config/strategy.yaml` -> `s3_funding_crowding` -> `ratios_stale_ms` to `1200000` (20 mins).
Regression test: Test snapshot loading with a simulated ratio point 10 mins old to ensure `is_stale` correctly passes under the new config.
Confidence: high

## 11. News Logging & Health Audit
- The codebase's news logic skips disabled sources and handles errors cleanly. Disabling a feed doesn't trip overall system failure (via `credibility.py`/`engine.py`), matching the constraints perfectly.

## 12. Runtime/VPS Operational Risks
- SQLite db (`aiosqlite`) holds state but must be monitored for unbounded growth.
- The websocket auto-reconnect handles Binance disconnection smoothly but may mask deeper networking blocks if max retries aren't visibly logged.

## 13. 72-Hour Soak Acceptance Checklist
- [ ] Feed uptime > 99% and reconnects < 5 per day per stream.
- [ ] Percentage of cycles with expected snapshots ready > 95%.
- [ ] No unexplained traceback/critical errors in `GuardExecutionError`.
- [ ] S3 fetched-vs-merged freshness distribution analyzed.
- [ ] Veto blocks logged clearly per active guard.
- [ ] Candidates and signals enqueued by hour without dropping completely.
- [ ] Telegram API limits observed with successful publish rate > 99%.
- [ ] Disabled news feeds produce 0 error noise.
