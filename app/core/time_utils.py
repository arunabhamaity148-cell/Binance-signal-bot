"""Time utilities.

The system tracks two distinct timestamp families everywhere data
flows through it:

- ``event_ts_ms``: the exchange-attributed timestamp of the data
  itself (e.g. a kline's close time, a trade's timestamp).
- ``received_ts_ms``: the local wall-clock time the message was
  ingested by this process.

Staleness checks compare both ``received_ts_ms - event_ts_ms``
(network/processing lag) and ``now_ms - received_ts_ms`` (age since
ingestion), because they catch different failure classes: a slow
upstream feed versus a stalled local consumer that stopped reading a
otherwise-healthy socket.

No strategy or guard reads the wall clock directly. Every calculation
takes ``now_ms`` as an explicit parameter (sourced from the
MarketSnapshot's ``as_of_ts_ms``) so behavior is deterministic and
testable without patching global time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


def now_ms() -> int:
    """Wall-clock time in milliseconds since epoch (UTC).

    This is the ONLY place in the codebase that reads the system
    clock directly. All other code receives time as an explicit
    parameter derived from a call to this function at a well-defined
    point (e.g. snapshot assembly), which keeps strategies and guards
    pure and deterministic given a fixed snapshot.
    """
    return int(time.time() * 1000)


@dataclass(frozen=True)
class TimestampPair:
    """A single (event, received) timestamp pair for one message."""

    event_ts_ms: int
    received_ts_ms: int

    def processing_lag_ms(self) -> int:
        """Time between the exchange's own timestamp and our ingestion."""
        return self.received_ts_ms - self.event_ts_ms

    def age_ms(self, as_of_ts_ms: int) -> int:
        """Time since ingestion, relative to a supplied reference clock."""
        return as_of_ts_ms - self.received_ts_ms


def is_stale(
    *,
    event_ts_ms: int,
    received_ts_ms: int,
    as_of_ts_ms: int,
    staleness_budget_ms: int,
) -> bool:
    """Return True if data is stale under either freshness definition.

    Data is stale if either:
      1. it arrived so late relative to its own event time that it is
         already outside the staleness budget on arrival, or
      2. it has aged past the staleness budget since it was received,
         relative to the current evaluation clock.

    Both checks use the same budget by design: the budget represents
    "how old can this information be and still be trustworthy," and it
    does not matter mechanically whether the lag was in transit or in
    the consumer.
    """
    pair = TimestampPair(event_ts_ms=event_ts_ms, received_ts_ms=received_ts_ms)
    if pair.processing_lag_ms() > staleness_budget_ms:
        return True
    if pair.age_ms(as_of_ts_ms) > staleness_budget_ms:
        return True
    return False


def utc_date_str(ts_ms: int) -> str:
    """UTC date as YYYYMMDD, used for signal_id construction."""
    return time.strftime("%Y%m%d", time.gmtime(ts_ms / 1000.0))


def iso8601_utc(ts_ms: int) -> str:
    """ISO 8601 UTC timestamp string with millisecond precision."""
    seconds = ts_ms // 1000
    millis = ts_ms % 1000
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds))
    return f"{base}.{millis:03d}Z"


def minutes_to_ms(minutes: float) -> int:
    return int(minutes * 60_000)


def hours_to_ms(hours: float) -> int:
    return int(hours * 3_600_000)


def ms_to_minutes(ms: int) -> float:
    return ms / 60_000.0
