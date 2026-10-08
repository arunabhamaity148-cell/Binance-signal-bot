from __future__ import annotations

import pytest

from app.backtest.walk_forward import (
    HoldoutGuard,
    HoldoutViolationError,
    assign_regime_bucket,
    build_anchored_folds,
    select_parameters,
)


def test_build_anchored_folds_produces_at_least_six():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    assert len(plan.folds) >= 6


def test_folds_are_anchored_all_train_windows_start_at_zero():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    for fold in plan.folds:
        assert fold.train_start_idx == 0


def test_folds_are_expanding_train_end_increases_monotonically():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    train_ends = [f.train_end_idx for f in plan.folds]
    assert train_ends == sorted(train_ends)
    assert len(set(train_ends)) == len(train_ends)  # strictly increasing, no ties


def test_embargo_gap_exists_between_train_and_test():
    plan = build_anchored_folds(total_length=10000, min_folds=6, embargo_pct=0.01)
    expected_embargo = max(1, int(10000 * 0.01))
    assert plan.embargo_bars == expected_embargo
    for fold in plan.folds:
        gap = fold.test_start_idx - fold.train_end_idx
        assert gap == expected_embargo


def test_test_windows_do_not_overlap_each_other():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    for i in range(len(plan.folds) - 1):
        assert plan.folds[i].test_end_idx <= plan.folds[i + 1].test_start_idx


def test_holdout_is_untouched_by_any_fold():
    plan = build_anchored_folds(total_length=10000, min_folds=6, holdout_pct=0.15)
    for fold in plan.folds:
        assert fold.train_end_idx <= plan.holdout_start_idx
        assert fold.test_end_idx <= plan.holdout_start_idx


def test_holdout_is_final_portion_of_dataset():
    plan = build_anchored_folds(total_length=10000, min_folds=6, holdout_pct=0.15)
    assert plan.holdout_end_idx == 10000
    assert plan.holdout_start_idx == int(10000 * 0.85)


def test_insufficient_data_raises_rather_than_fabricating_folds():
    with pytest.raises(ValueError):
        build_anchored_folds(total_length=10, min_folds=6)


def test_zero_total_length_raises():
    with pytest.raises(ValueError):
        build_anchored_folds(total_length=0, min_folds=6)


def test_invalid_holdout_pct_raises():
    with pytest.raises(ValueError):
        build_anchored_folds(total_length=10000, min_folds=6, holdout_pct=1.5)


def test_invalid_embargo_pct_raises():
    with pytest.raises(ValueError):
        build_anchored_folds(total_length=10000, min_folds=6, embargo_pct=-0.1)


def test_more_folds_requested_than_fit_raises():
    with pytest.raises(ValueError):
        build_anchored_folds(total_length=100, min_folds=50)


# ---------------------------------------------------------------------------
# Holdout protection: THE critical requirement
# ---------------------------------------------------------------------------


def test_holdout_guard_raises_on_overlap():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    with pytest.raises(HoldoutViolationError):
        guard.check(plan.holdout_start_idx, plan.holdout_start_idx + 100)


def test_holdout_guard_raises_on_partial_overlap():
    """A range that starts before the holdout but extends INTO it must
    still raise — partial overlap is still a violation."""
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    with pytest.raises(HoldoutViolationError):
        guard.check(plan.holdout_start_idx - 50, plan.holdout_start_idx + 50)


def test_holdout_guard_passes_for_in_sample_range():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    guard.check(0, plan.holdout_start_idx - 1)  # must not raise


def test_holdout_guard_boundary_exact_start_raises():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    with pytest.raises(HoldoutViolationError):
        guard.check(plan.holdout_start_idx, plan.holdout_start_idx + 1)


def test_holdout_guard_boundary_just_before_start_passes():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    guard.check(plan.holdout_start_idx - 1, plan.holdout_start_idx)  # must not raise


def test_select_parameters_raises_for_holdout_overlapping_range():
    """THE explicit requirement: attempting to fit on holdout raises."""
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)

    def fit_fn(**kwargs):
        return "fitted parameters"

    with pytest.raises(HoldoutViolationError):
        select_parameters(
            start_idx=plan.holdout_start_idx + 10, end_idx=plan.holdout_start_idx + 200,
            guard=guard, selector_fn=fit_fn,
        )


def test_select_parameters_succeeds_for_in_sample_range():
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)

    def fit_fn(value):
        return value * 2

    result = select_parameters(start_idx=0, end_idx=plan.holdout_start_idx - 1, guard=guard, selector_fn=fit_fn, value=21)
    assert result == 42


def test_select_parameters_never_calls_selector_fn_when_violating():
    """The selector function must not even be invoked when the range
    overlaps the holdout -- the guard check happens BEFORE the call,
    not just around logging it."""
    plan = build_anchored_folds(total_length=10000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    called = {"n": 0}

    def fit_fn():
        called["n"] += 1
        return "should never run"

    with pytest.raises(HoldoutViolationError):
        select_parameters(
            start_idx=plan.holdout_start_idx, end_idx=plan.holdout_end_idx, guard=guard, selector_fn=fit_fn,
        )
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Regime bucketing
# ---------------------------------------------------------------------------


def test_assign_regime_bucket_low():
    assert assign_regime_bucket(0.1) == "low_vol"


def test_assign_regime_bucket_mid():
    assert assign_regime_bucket(0.5) == "mid_vol"


def test_assign_regime_bucket_high():
    assert assign_regime_bucket(0.9) == "high_vol"


def test_assign_regime_bucket_boundaries():
    assert assign_regime_bucket(0.33) == "mid_vol"
    assert assign_regime_bucket(0.659999) == "mid_vol"
    assert assign_regime_bucket(0.66) == "high_vol"


def test_assign_regime_bucket_out_of_range_raises():
    with pytest.raises(ValueError):
        assign_regime_bucket(1.5)
    with pytest.raises(ValueError):
        assign_regime_bucket(-0.1)
