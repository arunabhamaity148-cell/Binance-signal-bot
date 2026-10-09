"""Normalization: raw Binance wire models -> core domain models.

This is the single seam between "what Binance sends" and "what the
rest of the system understands." Every conversion here also performs
structural validation appropriate to guard G1 (data integrity): out of
order sequences, duplicate timestamps, and OHLC violations are caught
here, as early as possible, rather than downstream in strategies.
"""

from __future__ import annotations

from app.core.errors import DataIntegrityError
from app.core.math import OHLC
from app.core.models import SymbolKlines, TimestampedValue
from app.data.binance.models import RawFundingRate, RawKline, RawOpenInterest, RawLongShortRatio, RawTakerLongShortRatio


def kline_to_ohlc(raw: RawKline) -> OHLC:
    return OHLC(
        open=raw.open,
        high=raw.high,
        low=raw.low,
        close=raw.close,
        volume=raw.volume,
        close_time_ms=raw.close_time_ms,
    )


def normalize_kline_series(symbol: str, timeframe: str, raw_klines: list[RawKline]) -> SymbolKlines:
    """Build a validated, ordered SymbolKlines from raw REST/WS klines.

    Only closed klines are retained (an unclosed forming bar from a WS
    stream is dropped here, never passed downstream — no strategy is
    permitted to read a forming bar per the no-look-ahead principle).

    Raises DataIntegrityError on out-of-order or duplicate close times,
    or on OHLC violations (surfaced via OHLC's own validation).
    """
    closed = [r for r in raw_klines if r.is_closed]
    seen_by_close_time: dict[int, RawKline] = {}
    bars: list[OHLC] = []
    prev_close_time: int | None = None
    for raw in closed:
        previous = seen_by_close_time.get(raw.close_time_ms)
        if previous is not None:
            if previous != raw:
                raise DataIntegrityError(
                    f"{symbol} {timeframe}: conflicting duplicate kline close_time {raw.close_time_ms}"
                )
            continue
        if prev_close_time is not None and raw.close_time_ms <= prev_close_time:
            raise DataIntegrityError(
                f"{symbol} {timeframe}: out-of-order or duplicate kline close_time "
                f"{raw.close_time_ms} <= previous {prev_close_time}"
            )
        seen_by_close_time[raw.close_time_ms] = raw
        prev_close_time = raw.close_time_ms
        bars.append(kline_to_ohlc(raw))

    return SymbolKlines(symbol=symbol, timeframe=timeframe, bars=bars)


def merge_kline_series(existing: SymbolKlines, new_raw: list[RawKline]) -> SymbolKlines:
    """Merge newly-arrived closed klines into an existing series,
    preserving order and rejecting structural violations. Used to
    append live WS-closed bars onto a REST-backfilled history."""
    combined_raw: list[RawKline] = []
    # Reconstruct RawKline-equivalent ordering isn't needed; instead
    # merge at the OHLC level directly for existing bars and validate
    # the new ones against the tail.
    existing_close_times = {b.close_time_ms for b in existing.bars}
    new_closed = [r for r in new_raw if r.is_closed]

    prev_close_time = existing.bars[-1].close_time_ms if existing.bars else None
    new_bars: list[OHLC] = []
    seen_new: dict[int, RawKline] = {}
    for raw in new_closed:
        if raw.close_time_ms in existing_close_times:
            continue
        previous = seen_new.get(raw.close_time_ms)
        if previous is not None:
            if previous != raw:
                raise DataIntegrityError(
                    f"{existing.symbol} {existing.timeframe}: conflicting duplicate merge close_time {raw.close_time_ms}"
                )
            continue
        if prev_close_time is not None and raw.close_time_ms <= prev_close_time:
            raise DataIntegrityError(
                f"{existing.symbol} {existing.timeframe}: out-of-order merge, "
                f"close_time {raw.close_time_ms} <= previous {prev_close_time}"
            )
        seen_new[raw.close_time_ms] = raw
        prev_close_time = raw.close_time_ms
        new_bars.append(kline_to_ohlc(raw))

    return SymbolKlines(
        symbol=existing.symbol,
        timeframe=existing.timeframe,
        bars=existing.bars + new_bars,
    )


def funding_rate_to_timestamped(raw: RawFundingRate, received_ts_ms: int) -> TimestampedValue:
    return TimestampedValue(
        value=raw.funding_rate, event_ts_ms=raw.funding_time_ms, received_ts_ms=received_ts_ms
    )


def open_interest_to_timestamped(raw: RawOpenInterest, received_ts_ms: int) -> TimestampedValue:
    return TimestampedValue(
        value=raw.open_interest, event_ts_ms=raw.timestamp_ms, received_ts_ms=received_ts_ms
    )


def long_short_ratio_to_timestamped(raw: RawLongShortRatio, received_ts_ms: int) -> TimestampedValue:
    return TimestampedValue(
        value=raw.long_short_ratio, event_ts_ms=raw.timestamp_ms, received_ts_ms=received_ts_ms
    )


def taker_long_short_ratio_to_timestamped(
    raw: RawTakerLongShortRatio, received_ts_ms: int
) -> TimestampedValue:
    return TimestampedValue(
        value=raw.taker_buy_sell_ratio, event_ts_ms=raw.ts_ms, received_ts_ms=received_ts_ms
    )


def normalize_timestamped_series(
    raws: list, converter, received_ts_ms: int
) -> list[TimestampedValue]:
    """Generic helper: convert a list of raw REST rows to a
    chronologically ordered, deduplicated TimestampedValue series."""
    values = [converter(r, received_ts_ms) for r in raws]
    values_sorted = sorted(values, key=lambda v: v.event_ts_ms)
    deduped: list[TimestampedValue] = []
    seen: set[int] = set()
    for v in values_sorted:
        if v.event_ts_ms in seen:
            continue
        seen.add(v.event_ts_ms)
        deduped.append(v)
    return deduped
