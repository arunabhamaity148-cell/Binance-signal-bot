"""Derivatives state assembly: funding rate, open interest (multiple
granularities), and long/short ratio series, normalized into
DerivativesState for inclusion in a MarketSnapshot.

HYBRID OPEN-INTEREST SOURCE (why two OI sources, not one)
----------------------------------------------------------
Binance USDS-M exposes open interest through two different public
endpoints with fundamentally different freshness characteristics:

  /futures/data/openInterestHist  ("hist")
      Pre-aggregated into fixed buckets (5m/15m/1h/1d). This is a
      HISTORY endpoint: a new 5m bucket only appears once that 5-minute
      window has closed, so the newest available point can be up to
      ~5 minutes (300_000 ms) old relative to "now" purely by the
      endpoint's own bucketing -- there is no way to make this endpoint
      fresher by polling it more often. It IS the right source for the
      historical series every strategy's percentile/window
      calculations need (S3's oi_percentile, S4/S5's OI-delta windows,
      G15's 30-day percentile, etc.).

  /fapi/v1/openInterest  ("live")
      Returns the single current OI value, computed by the exchange in
      real time, with no bucketing delay. This is the right source for
      "what is OI right now" -- exactly the question guards like G5
      (single-bar OI anomaly) and G13/G14 (current-vs-recent divergence
      /stagnation) are asking, and the question every strategy's
      "current OI point" (the last element of open_interest_history_5m)
      is implicitly answering when it feeds a freshness/staleness
      check.

Using "hist" alone for both purposes means the "current" OI point is
frequently ~2-5 minutes stale purely by bucketing, which is what
originally forced oi_stale_ms up to a value loose enough to never fire
usefully, or (as found in Batch 2/3 review) tight enough to hard-block
most evaluation cycles. Using "live" alone would lose the bucketed
historical series entirely.

The fix: fetch both. "hist" backfills and periodically gap-fills the
open_interest_history_* series (see `POLL_INTERVAL_LIVE_OI_S` and the
gap-fill cadence in app/data/snapshot.py's caller); "live" is polled
every POLL_INTERVAL_LIVE_OI_S seconds per symbol and its value is
APPENDED as the newest point of open_interest_history_5m (see
`merge_live_oi_into_5m_series` below), so the "current" point every
guard/strategy reads is always live-fresh, while the rest of the 5m
series still reflects the bucketed history for window calculations
that need several bars back.

STALENESS THRESHOLD DERIVATION
-------------------------------
POLL_INTERVAL_LIVE_OI_S = 30 (seconds). oi_stale_ms = 90_000 ms = 3x
the poll interval: tolerates up to 2 consecutive missed polls (network
hiccup, transient rate-limit backoff) plus jitter before treating the
live OI point as stale and failing closed. This mirrors the same
3x-poll-interval convention used nowhere else yet in this codebase but
is the standard, defensible choice for a periodic-poll freshness budget
(it is still class E -- an engineering safety margin -- not an
empirically calibrated value).

REST WEIGHT BUDGET CHECK (performed before implementing this, per
explicit instruction -- see the Batch 3 review conversation for the
full arithmetic): /fapi/v1/openInterest has a documented request
weight of 1. Polling 20 symbols every 30s is 20 x (60/30) = 40 calls/
min = 40 weight/min against the account's 2400 weight/min IP limit
(system.yaml's rest_weight_per_minute), i.e. ~1.7% of budget. This
leaves ample headroom for the "hist" gap-fill (issued far less
frequently, every 5 minutes) and every other REST consumer in this
system. No cadence had to be changed to fit the budget.
"""

from __future__ import annotations

from app.core.models import DerivativesState, TimestampedValue
from app.data.binance.models import (
    RawFundingRate,
    RawLongShortRatio,
    RawOpenInterest,
)
from app.data.normalization import (
    funding_rate_to_timestamped,
    long_short_ratio_to_timestamped,
    normalize_timestamped_series,
    open_interest_to_timestamped,
)

# Live /fapi/v1/openInterest poll cadence, per symbol (class E).
POLL_INTERVAL_LIVE_OI_S = 30

# oi_stale_ms = 3 x POLL_INTERVAL_LIVE_OI_S x 1000. Kept as a literal in
# config/veto.yaml and config/strategy.yaml (single source of config
# truth there), NOT read from this constant at runtime -- this constant
# exists so the derivation is checked by a test
# (test_oi_stale_ms_matches_three_times_poll_interval) rather than only
# asserted in a comment.
EXPECTED_OI_STALE_MS = 3 * POLL_INTERVAL_LIVE_OI_S * 1000


def build_derivatives_state(
    symbol: str,
    *,
    funding_rows: list[RawFundingRate],
    oi_5m_rows: list[RawOpenInterest],
    oi_15m_rows: list[RawOpenInterest],
    oi_1h_rows: list[RawOpenInterest],
    oi_1d_rows: list[RawOpenInterest],
    long_short_account_rows: list[RawLongShortRatio],
    taker_long_short_rows: list[RawLongShortRatio],
    premium_index_current: RawOpenInterest | None,
    received_ts_ms: int,
) -> DerivativesState:
    """Assemble a DerivativesState from raw REST rows for one symbol.

    Every series is chronologically ordered, deduplicated, and
    normalized to TimestampedValue via normalize_timestamped_series.
    Callers (data/snapshot.py) are responsible for supplying only rows
    that passed staleness checks; this function does not itself judge
    freshness (that is the responsibility of guards and strategies,
    which are given the as_of_ts_ms to compare against).
    """
    funding_history = normalize_timestamped_series(
        funding_rows, funding_rate_to_timestamped, received_ts_ms
    )
    oi_5m = normalize_timestamped_series(oi_5m_rows, open_interest_to_timestamped, received_ts_ms)
    oi_15m = normalize_timestamped_series(oi_15m_rows, open_interest_to_timestamped, received_ts_ms)
    oi_1h = normalize_timestamped_series(oi_1h_rows, open_interest_to_timestamped, received_ts_ms)
    oi_1d = normalize_timestamped_series(oi_1d_rows, open_interest_to_timestamped, received_ts_ms)
    ls_account = normalize_timestamped_series(
        long_short_account_rows, long_short_ratio_to_timestamped, received_ts_ms
    )
    taker_ls = normalize_timestamped_series(
        taker_long_short_rows, long_short_ratio_to_timestamped, received_ts_ms
    )

    premium_tv: TimestampedValue | None = None
    if premium_index_current is not None:
        premium_tv = open_interest_to_timestamped(premium_index_current, received_ts_ms)

    return DerivativesState(
        symbol=symbol,
        funding_rate_history=funding_history,
        open_interest_history_5m=oi_5m,
        open_interest_history_15m=oi_15m,
        open_interest_history_1h=oi_1h,
        open_interest_history_1d=oi_1d,
        long_short_account_ratio_history=ls_account,
        taker_long_short_ratio_history=taker_ls,
        premium_index_current=premium_tv,
    )


def merge_live_oi_into_5m_series(
    hist_5m_series: list[TimestampedValue],
    live_point: TimestampedValue,
) -> list[TimestampedValue]:
    """Append (or replace) the newest point of the bucketed 5m OI
    history with a fresher live-polled value, per the hybrid-source
    design above.

    `hist_5m_series` must be chronologically ordered oldest-first (as
    produced by `build_derivatives_state` / `normalize_timestamped_series`).
    `live_point` is the most recent /fapi/v1/openInterest poll result.

    Behavior:
      - If `live_point` is newer than the series' last entry, it is
        appended: the series now ends on the live-fresh value.
      - If a "hist" bucket has since arrived for the same window (rare
        race between the periodic hist gap-fill and a live poll), the
        function keeps whichever point is newer by event_ts_ms, so a
        genuinely newer hist bucket is never silently discarded in
        favor of a stale-by-comparison live point.
      - If `live_point` is not newer than the last entry, the series is
        returned unchanged (the live poll brought nothing new, e.g. a
        duplicate or out-of-order response).

    Returns a new list; does not mutate the input.
    """
    if not hist_5m_series:
        return [live_point]

    last = hist_5m_series[-1]
    if live_point.event_ts_ms > last.event_ts_ms:
        return hist_5m_series + [live_point]
    if live_point.event_ts_ms == last.event_ts_ms and live_point.received_ts_ms > last.received_ts_ms:
        # Same bucket window, but the live poll is a more recent read of
        # it than what we already have -- refresh receipt time so
        # staleness checks reflect the newer observation.
        return hist_5m_series[:-1] + [live_point]
    return list(hist_5m_series)


def oi_series_for_window(series: list[TimestampedValue], as_of_ts_ms: int, max_age_ms: int) -> list[float]:
    """Extract just the numeric OI values from a TimestampedValue
    series, filtering out anything older than max_age_ms relative to
    as_of_ts_ms. Ordered oldest-first."""
    return [
        tv.value
        for tv in series
        if (as_of_ts_ms - tv.received_ts_ms) <= max_age_ms
    ]
