from __future__ import annotations

from app.core.models import TimestampedValue
from app.data.binance.models import RawFundingRate, RawLongShortRatio, RawOpenInterest
from app.data.derivatives import build_derivatives_state, oi_series_for_window


def test_build_derivatives_state_assembles_all_series():
    funding = [RawFundingRate(symbol="BTCUSDT", funding_rate=0.0001, funding_time_ms=1000)]
    oi5 = [RawOpenInterest(symbol="BTCUSDT", open_interest=1000.0, timestamp_ms=1000)]
    ls = [RawLongShortRatio(symbol="BTCUSDT", long_short_ratio=1.2, timestamp_ms=1000)]

    state = build_derivatives_state(
        "BTCUSDT",
        funding_rows=funding,
        oi_5m_rows=oi5,
        oi_15m_rows=[],
        oi_1h_rows=[],
        oi_1d_rows=[],
        long_short_account_rows=ls,
        taker_long_short_rows=ls,
        premium_index_current=None,
        received_ts_ms=2000,
    )
    assert state.symbol == "BTCUSDT"
    assert len(state.funding_rate_history) == 1
    assert len(state.open_interest_history_5m) == 1
    assert state.premium_index_current is None


def test_build_derivatives_state_with_premium_index():
    premium = RawOpenInterest(symbol="BTCUSDT", open_interest=0.0002, timestamp_ms=1000)
    state = build_derivatives_state(
        "BTCUSDT",
        funding_rows=[],
        oi_5m_rows=[],
        oi_15m_rows=[],
        oi_1h_rows=[],
        oi_1d_rows=[],
        long_short_account_rows=[],
        taker_long_short_rows=[],
        premium_index_current=premium,
        received_ts_ms=2000,
    )
    assert state.premium_index_current is not None
    assert state.premium_index_current.value == 0.0002


def test_oi_series_for_window_filters_stale():
    from app.core.models import TimestampedValue

    series = [
        TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000),
        TimestampedValue(value=200.0, event_ts_ms=5000, received_ts_ms=5000),
    ]
    result = oi_series_for_window(series, as_of_ts_ms=6000, max_age_ms=2000)
    assert result == [200.0]


def test_oi_series_for_window_keeps_all_when_fresh():
    from app.core.models import TimestampedValue

    series = [
        TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000),
        TimestampedValue(value=200.0, event_ts_ms=2000, received_ts_ms=2000),
    ]
    result = oi_series_for_window(series, as_of_ts_ms=2500, max_age_ms=10000)
    assert result == [100.0, 200.0]


def test_expected_oi_stale_ms_matches_three_times_poll_interval():
    from app.data.derivatives import EXPECTED_OI_STALE_MS, POLL_INTERVAL_LIVE_OI_S

    assert POLL_INTERVAL_LIVE_OI_S == 30
    assert EXPECTED_OI_STALE_MS == 90_000
    assert EXPECTED_OI_STALE_MS == 3 * POLL_INTERVAL_LIVE_OI_S * 1000


def test_configured_oi_stale_ms_matches_derivation():
    """The actual configured value in every YAML that carries
    oi_stale_ms must equal the documented 3x-poll-interval derivation,
    not just a comment claiming it does."""
    from app.config import load_all
    from app.data.derivatives import EXPECTED_OI_STALE_MS

    cfg = load_all()
    assert cfg.system["staleness_budget_ms"]["oi_ms"] == EXPECTED_OI_STALE_MS
    assert cfg.veto["g5_oi_anomaly"]["oi_stale_ms"] == EXPECTED_OI_STALE_MS
    for strat_key in ("s3_funding_crowding", "s4_oi_trend", "s5_oi_regime"):
        assert cfg.strategy[strat_key]["oi_stale_ms"] == EXPECTED_OI_STALE_MS


def test_merge_live_oi_appends_newer_point():
    from app.data.derivatives import merge_live_oi_into_5m_series

    hist = [
        TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000),
        TimestampedValue(value=110.0, event_ts_ms=2000, received_ts_ms=2000),
    ]
    live = TimestampedValue(value=115.0, event_ts_ms=2100, received_ts_ms=2100)
    merged = merge_live_oi_into_5m_series(hist, live)
    assert merged == hist + [live]
    assert merged is not hist  # does not mutate input


def test_merge_live_oi_on_empty_history():
    from app.data.derivatives import merge_live_oi_into_5m_series

    live = TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000)
    assert merge_live_oi_into_5m_series([], live) == [live]


def test_merge_live_oi_same_bucket_newer_receipt_refreshes_last():
    from app.data.derivatives import merge_live_oi_into_5m_series

    hist = [TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000)]
    live = TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1500)
    merged = merge_live_oi_into_5m_series(hist, live)
    assert len(merged) == 1
    assert merged[0].received_ts_ms == 1500


def test_merge_live_oi_stale_live_point_does_not_regress_series():
    from app.data.derivatives import merge_live_oi_into_5m_series

    hist = [
        TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000),
        TimestampedValue(value=110.0, event_ts_ms=2000, received_ts_ms=2000),
    ]
    stale_live = TimestampedValue(value=999.0, event_ts_ms=500, received_ts_ms=500)
    merged = merge_live_oi_into_5m_series(hist, stale_live)
    assert merged == hist


def test_merge_live_oi_newer_hist_bucket_not_discarded_by_older_live_point():
    """Race case: a genuinely newer hist bucket must win over a live
    point that (by event_ts_ms) is actually for an earlier window."""
    from app.data.derivatives import merge_live_oi_into_5m_series

    hist = [
        TimestampedValue(value=100.0, event_ts_ms=1000, received_ts_ms=1000),
        TimestampedValue(value=110.0, event_ts_ms=2000, received_ts_ms=5000),
    ]
    older_live = TimestampedValue(value=105.0, event_ts_ms=1500, received_ts_ms=1500)
    merged = merge_live_oi_into_5m_series(hist, older_live)
    assert merged == hist
