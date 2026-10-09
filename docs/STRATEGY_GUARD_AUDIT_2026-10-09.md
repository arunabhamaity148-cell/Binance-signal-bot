# Strategy and Veto-Guard Audit

**Date:** 2026-10-09  
**Repository:** `Binance-signal-bot`  
**Scope:** S2-S5, G1-G15, live evaluation wiring, logging, confidence behavior, data-integrity paths  
**Baseline:** 830 tests passing before this audit

## Executive summary

The audit found and corrected production-impacting issues in the live veto context, S3 hard-rule enforcement, kline integrity normalization, G8 history sizing, G14 configurability, and operator-facing diagnostics. The audit also added explicit per-guard regression modules and synthetic weak/medium/strong confidence tests for S2-S5.

## Findings and disposition

| Area | Finding | Disposition |
|---|---|---|
| S2 | Candidate confidence was not logged with its input factors. | Added `s2_confidence_breakdown` structured logging and synthetic confidence tests. |
| S3 | `_assert_never_funding_alone()` was called with literal `True` values, making the assertion vacuous. | Assertion inputs now derive from funding/OI/price thresholds and candidate metadata for structure break and taker flip. |
| S3 | Confidence and reversal prerequisites lacked consistent operator diagnostics. | Added structured confidence logging and recorded `structure_break` / `taker_flip` in metadata. |
| S4 | Slope explanation said `ATR/bar` even though the calculation is normalized over the configured lookback. | Message now states `ATR over N bars`; added confidence logging and metadata. |
| S5 | Candidate confidence factors were not logged. | Added structured confidence logging and confidence metadata. |
| G1 | Kline normalization sorted input before validation, so out-of-order data was silently accepted; conflicting duplicate payloads were silently discarded. | Normalization and merge paths now fail closed on out-of-order or conflicting duplicates while retaining identical retransmit idempotency. |
| G2 | Live WebSocket reconnect thresholds were not sourced from veto configuration. | `LiveSnapshotCache` now passes configured reconnect count and window to the WebSocket client. |
| G3 | Live orderbook construction used a hardcoded depth level. | Snapshot construction now reads `g3_depth_collapse.depth_check_levels`. |
| G6 | Live `evaluate_symbol()` always passed `funding_z=None`. | Funding z-score is computed from the live snapshot’s configured S3 funding window and passed to the veto engine. |
| G8 | The percentile history slice could contain 199 prior ATR values despite the 200-value requirement. | Requires 201 ATR-series points before evaluating 200 prior values. |
| G9 | Live `evaluate_symbol()` always passed `btc_trend_direction=None`. | BTC regime is computed once per symbol evaluation cycle from the ready BTC snapshot and passed to G9. Missing/invalid BTC context remains fail-safe. |
| G14 | Stagnation lookback was hardcoded to 12 bars. | Added `window_bars` to `config/veto.yaml`, with a backward-compatible default of 12. |
| G14/G15 | Reasons were technically informative but not consistently explicit for operators. | Messages now include explicit `G14 OI stagnation` and `G15 OI percentile extreme` labels. |
| G4, G5, G7, G10, G11, G12, G13, G15 | No additional production defect required for the audited behavior; coverage was expanded with dedicated guard tests. | Added focused regression modules and retained existing isolation/edge-case coverage. |

## Tests added

- `tests/unit/test_g1_data_integrity.py` through `tests/unit/test_g15_oi_percentile_extreme.py` — one focused module per guard.
- `tests/unit/test_strategy_confidence_s2_s5.py` — weak, medium, and strong synthetic candidate confidence checks for S2, S3, S4, and S5.
- Updated normalization expectations to reflect the intentional fail-closed integrity contract.

## Validation

```text
pytest -q
860 passed in 10.77s

SIGNAL-ONLY SCAN: PASS
PLACEHOLDER SCAN: PASS
FORBIDDEN CALLS SCAN: PASS
CONFIG VALIDATION: PASS (all seven config files valid)
git diff --check: PASS
```

## Safety assessment

No order placement, position modification, trading credential, user-data stream, or private Binance endpoint was introduced. The signal-only scanners remain green. No commit or GitHub push was performed as part of this audit.
