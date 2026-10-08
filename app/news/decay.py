"""Decay / half-life per category, per spec section 14's "decay /
half-life per category" and "stale-news rejection (per-category
half-life)" mandatory components.

Half-life values live in config/news_sources.yaml's
category_half_life_hours (all class F, per NEWS_REGISTRY.md — none
empirically validated). This module implements the decay math only;
it never hardcodes a half-life value.

Decay model: standard exponential half-life. An event's effective
weight at age `t` is `initial_weight * 0.5^(t / half_life)`. An event
is considered "active" (still counted for severity/corroboration
purposes) while its decayed weight is still above a floor — in
practice, the news engine (engine.py) treats "active" as "published
within some multiple of the half-life", which is a simpler and
equivalent-enough operational rule; see `is_active` below for the
exact cutoff used.
"""

from __future__ import annotations

import math

from app.core.models import NewsCategory


def get_half_life_ms(category: NewsCategory, category_half_life_hours: dict) -> int:
    """Look up the configured half-life for `category`, in
    milliseconds. Raises KeyError if the category has no configured
    half-life — every NewsCategory value must have an entry in
    config/news_sources.yaml's category_half_life_hours (the config
    validator enforces this at boot; see scripts/validate_config.py).
    """
    key = category.value
    if key not in category_half_life_hours:
        raise KeyError(f"no half-life configured for category {key!r}")
    hours = category_half_life_hours[key]
    return int(hours * 3_600_000)


def decayed_weight(initial_weight: float, age_ms: int, half_life_ms: int) -> float:
    """Exponential decay: initial_weight * 0.5^(age_ms / half_life_ms).

    age_ms must be >= 0 (an event cannot be "aged" into the future);
    callers are responsible for ensuring this (age is always
    as_of_ts_ms - published_ts_ms, and published_ts_ms should never be
    in the future relative to as_of_ts_ms for legitimately-fetched
    news — a violation here indicates a clock or data issue upstream,
    not something this pure function should silently paper over).
    """
    if age_ms < 0:
        raise ValueError(f"age_ms must be >= 0, got {age_ms}")
    if half_life_ms <= 0:
        raise ValueError(f"half_life_ms must be > 0, got {half_life_ms}")
    return initial_weight * math.pow(0.5, age_ms / half_life_ms)


def is_active(
    *,
    published_ts_ms: int,
    as_of_ts_ms: int,
    category: NewsCategory,
    category_half_life_hours: dict,
    active_half_life_multiple: float = 3.0,
) -> bool:
    """An event is "active" while its age is within
    `active_half_life_multiple` half-lives of publication (default 3,
    at which point decayed weight is 1/8 of initial — a reasonable,
    documented cutoff for "no longer practically relevant", distinct
    from the continuous decayed_weight value itself, which callers
    needing the exact weight should use directly rather than this
    boolean gate).

    `active_half_life_multiple` is a fixed engineering choice (not a
    class F/E config value — it's an implementation detail of what
    "active" means, not a market-relevant threshold) and is not
    exposed via config; STRATEGIES_SPEC.md / NEWS_REGISTRY.md do not
    specify an exact multiple, and the config schema does not carry a
    key for it, so it is defined here, once, rather than invented
    per-caller.
    """
    age_ms = as_of_ts_ms - published_ts_ms
    if age_ms < 0:
        return True  # not yet "aged" at all; treat as freshly active rather than erroring in a hot path
    half_life_ms = get_half_life_ms(category, category_half_life_hours)
    return age_ms <= active_half_life_multiple * half_life_ms
