from __future__ import annotations

import pytest

from app.core.errors import DataIntegrityError
from app.data.binance.models import RawFundingRate, RawKline
from app.data.normalization import (
    funding_rate_to_timestamped,
    merge_kline_series,
    normalize_kline_series,
    normalize_timestamped_series,
)


def _raw_kline(close_time_ms: int, close: float = 100.0, is_closed: bool = True) -> RawKline:
    return RawKline(
        open_time_ms=close_time_ms - 300_000,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=10.0,
        close_time_ms=close_time_ms,
        taker_buy_base_volume=5.0,
        is_closed=is_closed,
    )


def test_normalize_kline_series_rejects_out_of_order_input():
    raws = [_raw_kline(600_000), _raw_kline(300_000), _raw_kline(900_000)]
    with pytest.raises(DataIntegrityError):
        normalize_kline_series("BTCUSDT", "5m", raws)


def test_normalize_kline_series_drops_unclosed():
    raws = [_raw_kline(300_000), _raw_kline(600_000, is_closed=False)]
    sk = normalize_kline_series("BTCUSDT", "5m", raws)
    assert len(sk.bars) == 1


def test_normalize_kline_series_deduplicates_identical_close_time():
    raws = [_raw_kline(300_000), _raw_kline(300_000)]
    sk = normalize_kline_series("BTCUSDT", "5m", raws)
    assert len(sk.bars) == 1


def test_normalize_kline_series_rejects_conflicting_duplicate():
    """A conflicting retransmit must fail closed rather than be dropped."""
    raws = [_raw_kline(300_000, close=100.0), _raw_kline(300_000, close=999.0)]
    with pytest.raises(DataIntegrityError):
        normalize_kline_series("BTCUSDT", "5m", raws)


def test_merge_kline_series_appends_new_bars():
    existing = normalize_kline_series("BTCUSDT", "5m", [_raw_kline(300_000)])
    merged = merge_kline_series(existing, [_raw_kline(600_000)])
    assert [b.close_time_ms for b in merged.bars] == [300_000, 600_000]


def test_merge_kline_series_rejects_out_of_order():
    existing = normalize_kline_series("BTCUSDT", "5m", [_raw_kline(600_000)])
    with pytest.raises(DataIntegrityError):
        merge_kline_series(existing, [_raw_kline(300_000)])


def test_merge_kline_series_skips_already_present():
    existing = normalize_kline_series("BTCUSDT", "5m", [_raw_kline(300_000)])
    merged = merge_kline_series(existing, [_raw_kline(300_000)])
    assert len(merged.bars) == 1


def test_funding_rate_to_timestamped():
    raw = RawFundingRate(symbol="BTCUSDT", funding_rate=0.0001, funding_time_ms=1000)
    tv = funding_rate_to_timestamped(raw, received_ts_ms=1500)
    assert tv.value == 0.0001
    assert tv.event_ts_ms == 1000
    assert tv.received_ts_ms == 1500


def test_normalize_timestamped_series_orders_and_dedupes():
    raws = [
        RawFundingRate(symbol="BTCUSDT", funding_rate=0.0001, funding_time_ms=2000),
        RawFundingRate(symbol="BTCUSDT", funding_rate=0.0002, funding_time_ms=1000),
        RawFundingRate(symbol="BTCUSDT", funding_rate=0.0003, funding_time_ms=1000),
    ]
    series = normalize_timestamped_series(raws, funding_rate_to_timestamped, received_ts_ms=5000)
    assert [tv.event_ts_ms for tv in series] == [1000, 2000]
