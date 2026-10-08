# KNOWN_UNCERTAINTIES.md

This document exists so that nothing in this design is mistaken for a
validated claim. Per spec §32 ("DO NOT LIE"), everything listed here
is an open question, not a settled fact.

## 1. Every threshold classed [F] throughout the spec documents

There are roughly 60+ individual numeric thresholds marked class F
(arbitrary/unvalidated) across `STRATEGIES_SPEC.md`, `VETO_SPEC.md`,
and `CONFIG_SCHEMAS.md` — swing lookbacks, ATR multiples, percentile
cutoffs, z-score thresholds, grade boundaries, half-lives, and so on.
**None of these have been empirically validated against this specific
20-pair universe.** They are reasonable starting points drawn from
common technical-analysis and market-microstructure conventions, not
outputs of a calibration process. The roadmap (P2) requires
recalibration via backtest + shadow-mode before any of them can be
trusted or relabeled class C.

## 2. Effective-vote consensus grade thresholds (§11)

The A+/A/B boundaries (`weighted confidence >= 0.82`, `V_eff >= 2.5`,
etc.) are explicitly called "initial hypothesis, subject to shadow-mode
validation" in the spec itself. There is no evidence yet that these
particular cutoffs separate genuinely higher-quality setups from lower
ones. It's entirely possible the effective-vote formula itself needs
revision after seeing real distributions of `V_eff` and confidence
across the 5 strategies.

## 3. Per-symbol tier thresholds (depth, spread, slippage)

The `majors`/`mid_caps`/`small_caps` USD-depth and bps-spread
thresholds in G3/G4/G11 are placeholder estimates, not measurements.
Actual order-book depth on Binance USDⓈ-M for pairs like SUIUSDT,
ARBUSDT, TONUSDT varies meaningfully by time of day and market
conditions; these numbers need to be derived from actual sampled
order-book data before they can be trusted to correctly distinguish
"tradable" from "too thin," in either direction (too loose risks bad
fills; too tight risks blocking every legitimate signal on smaller
pairs).

## 4. News source URLs and free-tier availability

`config/news_sources.yaml` in `CONFIG_SCHEMAS.md` contains placeholder
URLs. Specifically:
- Reuters/Bloomberg "public" RSS feed availability and terms change
  over time; whether a genuinely free, ToS-compliant feed currently
  exists must be re-verified at implementation time, not assumed from
  this design document.
- CryptoPanic's free-tier terms and rate limits should be re-checked
  at implementation time.
- Binance's official announcement feed format (RSS vs JSON vs
  HTML-only) should be confirmed against current developer docs.

## 5. News category half-lives

The half-life table (regulatory: 6h, hack: 2h, etc.) is a reasonable
first approximation but is not derived from measured price-impact
decay curves for this asset universe. It's plausible some categories
decay faster or slower than modeled, especially for smaller-cap assets
where a single news item can have outsized and longer-lasting impact
relative to majors.

## 6. Fee/slippage cost model completeness

The cost model (fees + spread + slippage + latency) as described is
directionally correct but its slippage sub-model
(`estimated_slippage_bps(symbol, notional)`) needs an actual
implementation choice (e.g., linear impact vs. square-root impact vs.
empirical fill-based estimation from historical order-book snapshots)
that hasn't been fixed yet. Different reasonable choices could produce
meaningfully different R:R numbers for the same candidate, especially
on lower-liquidity pairs.

## 7. Structure/swing detection robustness

The HH/HL and LH/LL structure detection used in S4, and swing-high/low
detection used in S1, are defined at a conceptual level
("each swing high > prior swing high") but swing detection algorithms
are notoriously sensitive to the exact pivot-detection method (fixed
lookback vs. fractal vs. ZigZag-style). The spec doesn't yet pin down
which specific algorithm implements "swing," and different choices will
produce different candidate counts and quality. This needs to be fixed
concretely during implementation and then treated as a stable,
versioned definition (not silently tweaked later).

## 8. Correlated-cluster definition for exposure capping

`correlated_clusters` in `risk.yaml` currently defines only one
explicit cluster (BTC/ETH/SOL as "majors_beta"). Whether other pairs in
the 20-symbol universe exhibit high enough correlation to warrant
clustering (e.g., L1s like NEAR/APT/SUI/ATOM moving together in
alt-season conditions) is an empirical question not yet answered by
this design.

## 9. Whether 20 pairs at these strategies' frequency will hit rate
limits in practice

`rate_limit_budget.rest_weight_per_minute` in `system.yaml` is set
conservatively, but actual REST call volume depends on how much of the
derivatives data (funding/OI/ratios, several of which have no
WebSocket equivalent and must be polled) is needed per symbol per
strategy-evaluation cycle. This needs to be measured against real
polling intervals during implementation, not assumed to fit from the
design alone.

## 10. Backtest realism vs. live execution

Even a well-built cost- and slippage-aware backtest cannot fully
capture live execution reality (queue position for limit orders,
actual fill probability under real market stress, real Telegram
delivery latency to a human who then manually executes). The
acceptance gates in spec §22 validate the backtest's internal
consistency and the strategies' historical edge under modeled costs —
they are explicitly **not** a claim that live results will match, which
is why spec §31 requires distinguishing "OFFLINE VERIFIED" from "LIVE
VERIFIED" and why paper/shadow soak status must be reported separately
in the final release report.

## 11. Class-E gate rejection frequency across S1/S2/S5

Found during Batch 2/3 implementation and review (post-dates the
original Phase 1 design; items 1-10 above were written before any
strategy code existed). Recorded here because it is a pattern across
multiple strategies, not a single strategy's quirk, and because the
decision to leave it alone was explicit and deliberate, not an
oversight.

**The three observations:**

- **S1** (Batch 2.5): on a realistic BTC fixture (ATR(14) ≈ $202, ≈20
  bps stop distance), raw R:R at TP2 is exactly 2.0000 (confirming the
  entry-midpoint R convention is correctly implemented), but post-cost
  R:R lands at **1.693**, below `min_rr_tp2 = 1.8` (class F,
  `config/risk.yaml`).
- **S2** (Batch 3): `sl_boundary_buffer_atr = 0.1` (class F) produces a
  stop distance of only 0.1x ATR. Against `min_stop_cost_multiple =
  3.0` (class E, `config/strategy.yaml`), S2's stop only clears the
  safety filter when ATR is roughly **6-7x** the ATR level used in the
  S1 realistic fixture — i.e., only in a materially elevated-volatility
  regime (~136 bps ATR in the passing test fixture, vs. ~20 bps for
  S1's realistic case).
- **S5** (Batch 3): fixed `tp_r_multiples = [1.0, 2.0, 3.0, 4.0]` caps
  raw R:R at TP2 at exactly 2.0 by construction. After the 2x maker
  round-trip cost, clearing `min_rr_tp2 = 1.8` requires ATR ≳ **93
  bps** on a $100k-scale BTC fixture (`sl_buffer_atr = 0.6`, class F).

**Shared cause:** all three strategies compute stops as a fixed
multiple of ATR, and R:R is graded post-cost from the entry midpoint
(per the R CONVENTION documented in `app/strategies/base.py`, itself a
Batch 2/3 correction — see the Batch 2/3 review history for the
R-convention bug this fixed). When the ATR-based stop distance is
small in absolute (price) terms —
which happens at "normal" volatility on a high-priced asset like
BTC — the fixed round-trip cost (2x maker fee, or maker+taker+slippage
for sizing) becomes a large fraction of R. Tight stops on high-priced
assets inflate cost drag as a fraction of R; only a wider ATR (higher
realized volatility) gives the stop enough absolute room for the fixed
cost to stay a small fraction of it.

**The open question for shadow mode:** What fraction of the time do
S1, S2, and S5 clear their class-E/F gates on real market data, broken
down per pair (majors vs. mid-caps vs. small-caps have very different
price levels and so very different absolute-vs-relative cost dynamics)
and per volatility regime? If the answer is "rarely, except during
elevated-volatility windows," that may be an acceptable and even
desirable property (these strategies would then only fire when
conditions genuinely favor them) — or it may indicate the class-F
stop-construction parameters (`sl_boundary_buffer_atr`,
`sl_buffer_atr`, `tp_r_multiples`) are miscalibrated for the class-E
safety floor they're being measured against. Shadow-mode data is
required to distinguish these two explanations; neither can be
determined from synthetic fixtures or reasoning about the formulas
alone.

**Explicit statement — this is NOT to be resolved by tuning:** Neither
the class-E filters (`min_stop_cost_multiple`, entry-zone-width cap)
nor the class-F strategy parameters (`sl_boundary_buffer_atr`,
`sl_buffer_atr`, `tp_r_multiples`, `min_rr_tp2`) should be adjusted in
response to this observation without shadow-mode evidence. Doing so
now — before any real distribution exists — would be look-ahead bias:
tuning a threshold to make a known synthetic fixture pass is fitting
to the test, not to reality. This pattern is deliberately left as-is
pending real data.

**Relationship to item 1 above:** this is related to, but distinct
from, the general "all class-F thresholds are unvalidated" statement
in item 1. Item 1 says the *values themselves* haven't been
calibrated. This item says something more specific: there's a
*structural interaction* between three strategies' stop-construction
math and two class-E safety floors that makes rejection frequency
correlate with volatility regime in a way that's currently unmeasured.
Resolving item 1 (calibrating individual values) does not by itself
answer this item's question (what the *interaction* between several
values produces across real market conditions) — they need to be
tracked separately during shadow-mode review.

---

None of the above blocks producing a correct, fail-closed,
signal-only system. It blocks claiming that system is *calibrated* or
*proven profitable* — claims this design deliberately avoids making,
per spec §32.
