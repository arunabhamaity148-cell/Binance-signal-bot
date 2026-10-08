"""Signal expiry calculation.

Each grade (A+/A/B) has its own expiry window from config/risk.yaml's
`expiry_per_grade`. Expiry is computed once at signal construction
time and stored as an absolute timestamp (`expiry_ts_ms`) rather than
a duration, so downstream consumers (Telegram formatter, lifecycle
tracker) never need to re-derive it or depend on wall-clock reads.
"""

from __future__ import annotations

from app.core.time_utils import minutes_to_ms


def compute_expiry_ts_ms(*, created_ts_ms: int, grade: str, expiry_per_grade: dict[str, float]) -> int:
    if grade not in expiry_per_grade:
        raise ValueError(f"no expiry configured for grade {grade!r}")
    minutes = expiry_per_grade[grade]
    return created_ts_ms + minutes_to_ms(minutes)


def is_expired(*, as_of_ts_ms: int, expiry_ts_ms: int) -> bool:
    return as_of_ts_ms >= expiry_ts_ms
