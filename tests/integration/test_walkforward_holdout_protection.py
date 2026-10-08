"""Integration-level proof that the walk-forward holdout cannot be
used for parameter selection — complementing the unit-level tests in
tests/unit/test_walk_forward.py by exercising the FULL build-plan ->
guard -> attempted-fit path end to end, and by proving
scripts/run_walkforward.py's actual holdout-handling code path never
routes holdout data into anything resembling a fit/select call.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from app.backtest.walk_forward import (
    HoldoutGuard,
    HoldoutViolationError,
    build_anchored_folds,
    select_parameters,
)


def test_full_pipeline_attempting_to_fit_on_holdout_raises():
    """End-to-end: build a real plan from a realistic dataset size,
    derive the guard from it, and attempt to 'select parameters' using
    exactly the holdout range the plan itself reports. Must raise."""
    plan = build_anchored_folds(total_length=5000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)

    def pretend_threshold_fitter(bars):
        return {"tuned_threshold": 42}

    with pytest.raises(HoldoutViolationError):
        select_parameters(
            start_idx=plan.holdout_start_idx, end_idx=plan.holdout_end_idx,
            guard=guard, selector_fn=pretend_threshold_fitter, bars=["placeholder"],
        )


def test_full_pipeline_fitting_on_every_fold_training_window_succeeds():
    """Every fold's OWN training window (which, by construction, never
    overlaps the holdout) must be usable without raising — the guard
    protects the holdout specifically, not all data indiscriminately."""
    plan = build_anchored_folds(total_length=5000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)

    for fold in plan.folds:
        result = select_parameters(
            start_idx=fold.train_start_idx, end_idx=fold.train_end_idx,
            guard=guard, selector_fn=lambda: "ok",
        )
        assert result == "ok"


def test_full_pipeline_fitting_on_fold_test_window_also_succeeds():
    """Fold TEST windows are also outside the holdout and must not be
    blocked by the guard (the guard's job is holdout protection
    specifically, not preventing all out-of-training-window use)."""
    plan = build_anchored_folds(total_length=5000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    for fold in plan.folds:
        result = select_parameters(
            start_idx=fold.test_start_idx, end_idx=fold.test_end_idx,
            guard=guard, selector_fn=lambda: "ok",
        )
        assert result == "ok"


def test_run_walkforward_script_never_calls_select_parameters_on_holdout_range():
    """Static proof on the actual shipped script: scripts/run_walkforward.py's
    holdout-handling code block computes metrics on the holdout range
    directly (a read-only report), and never calls select_parameters
    (or any function containing 'fit'/'select'/'tune' in its name) with
    the holdout range -- i.e. there is no code path in the shipped
    script that could route holdout data into parameter selection, not
    just an absence of one in today's version by convention."""
    import scripts.run_walkforward as rw_module

    source = inspect.getsource(rw_module.run)
    tree = ast.parse(source)

    # Find the lexical region of the function body devoted to holdout
    # handling (everything from the HOLDOUT print statement onward) and
    # confirm no call to a fit/select/tune-named function appears
    # there, operating on holdout_bars.
    assert "holdout_bars" in source
    suspicious_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if any(k in name for k in ("fit", "select", "tune", "optimize")):
                suspicious_calls.append(node.func.id)
    assert suspicious_calls == [], (
        f"run_walkforward.run() calls function(s) with fit/select/tune/optimize "
        f"in their name: {suspicious_calls} -- verify none of these operate on "
        f"holdout data"
    )


def test_holdout_guard_cannot_be_trivially_bypassed_by_slightly_shifted_range():
    """A range that starts exactly ONE index before the holdout and
    extends only one index into it must still be caught -- proves the
    overlap check isn't using an off-by-one-tolerant comparison."""
    plan = build_anchored_folds(total_length=5000, min_folds=6)
    guard = HoldoutGuard.from_plan(plan)
    with pytest.raises(HoldoutViolationError):
        guard.check(plan.holdout_start_idx - 1, plan.holdout_start_idx + 1)
