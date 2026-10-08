from __future__ import annotations

import pytest

from app.config import load_all
from app.core.models import NewsCategory
from app.news.decay import decayed_weight, get_half_life_ms, is_active

HALF_LIFE_HOURS = load_all().news_sources["category_half_life_hours"]


def test_get_half_life_ms_regulatory():
    ms = get_half_life_ms(NewsCategory.REGULATORY, HALF_LIFE_HOURS)
    assert ms == HALF_LIFE_HOURS["regulatory"] * 3_600_000


def test_get_half_life_ms_all_categories_present():
    for category in NewsCategory:
        ms = get_half_life_ms(category, HALF_LIFE_HOURS)
        assert ms > 0


def test_get_half_life_ms_unknown_category_raises():
    with pytest.raises(KeyError):
        get_half_life_ms(NewsCategory.REGULATORY, {})


def test_decayed_weight_at_zero_age_is_full_weight():
    assert decayed_weight(1.0, 0, 3_600_000) == pytest.approx(1.0)


def test_decayed_weight_at_one_half_life_is_half():
    half_life = 3_600_000
    assert decayed_weight(1.0, half_life, half_life) == pytest.approx(0.5)


def test_decayed_weight_at_two_half_lives_is_quarter():
    half_life = 3_600_000
    assert decayed_weight(1.0, 2 * half_life, half_life) == pytest.approx(0.25)


def test_decayed_weight_negative_age_raises():
    with pytest.raises(ValueError):
        decayed_weight(1.0, -1, 1000)


def test_decayed_weight_zero_half_life_raises():
    with pytest.raises(ValueError):
        decayed_weight(1.0, 100, 0)


def test_decayed_weight_scales_with_initial_weight():
    half_life = 1000
    assert decayed_weight(2.0, half_life, half_life) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# is_active
# ---------------------------------------------------------------------------


def test_is_active_fresh_event():
    assert is_active(
        published_ts_ms=1000, as_of_ts_ms=1500,
        category=NewsCategory.HACK, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_is_active_within_three_half_lives():
    half_life_ms = get_half_life_ms(NewsCategory.OUTAGE, HALF_LIFE_HOURS)  # shortest half-life category
    as_of = half_life_ms * 2  # within 3x
    assert is_active(
        published_ts_ms=0, as_of_ts_ms=as_of,
        category=NewsCategory.OUTAGE, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_is_active_false_beyond_three_half_lives():
    half_life_ms = get_half_life_ms(NewsCategory.OUTAGE, HALF_LIFE_HOURS)
    as_of = half_life_ms * 4  # beyond 3x
    assert not is_active(
        published_ts_ms=0, as_of_ts_ms=as_of,
        category=NewsCategory.OUTAGE, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_is_active_exactly_at_boundary():
    half_life_ms = get_half_life_ms(NewsCategory.LIQUIDATION, HALF_LIFE_HOURS)
    as_of = half_life_ms * 3
    assert is_active(
        published_ts_ms=0, as_of_ts_ms=as_of,
        category=NewsCategory.LIQUIDATION, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_is_active_just_past_boundary():
    half_life_ms = get_half_life_ms(NewsCategory.LIQUIDATION, HALF_LIFE_HOURS)
    as_of = half_life_ms * 3 + 1
    assert not is_active(
        published_ts_ms=0, as_of_ts_ms=as_of,
        category=NewsCategory.LIQUIDATION, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_is_active_future_published_ts_treated_as_active():
    assert is_active(
        published_ts_ms=10_000, as_of_ts_ms=5_000,
        category=NewsCategory.HACK, category_half_life_hours=HALF_LIFE_HOURS,
    )


def test_different_categories_have_different_half_lives_reflected_in_activity():
    """Hack (short half-life) decays out faster than regulatory (long
    half-life) for the same age, per NEWS_REGISTRY.md's table."""
    hack_half_life = get_half_life_ms(NewsCategory.HACK, HALF_LIFE_HOURS)
    regulatory_half_life = get_half_life_ms(NewsCategory.REGULATORY, HALF_LIFE_HOURS)
    assert hack_half_life < regulatory_half_life

    age = hack_half_life * 4  # well past hack's 3x cutoff, likely still within regulatory's
    hack_active = is_active(published_ts_ms=0, as_of_ts_ms=age, category=NewsCategory.HACK, category_half_life_hours=HALF_LIFE_HOURS)
    assert not hack_active
