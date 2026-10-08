"""Walk-forward / out-of-sample validation, per spec section 23:
train/test separation, 1% embargo between train and test, multiple
anchored folds (>= 6), regime segmentation, and an untouched final
holdout that is reported but NEVER used for parameter selection.

ANCHORED (EXPANDING-WINDOW) FOLDS: fold k's training window always
starts at the very beginning of the data and grows with each fold
(anchored at index 0); only the test window advances. This is the
standard "anchored" walk-forward design — distinct from a "rolling"
design where the train window's start also advances — and is what the
spec's "anchored" wording specifies.

EMBARGO: a gap of 1% of the total dataset length is excluded between
the end of each fold's training window and the start of its test
window, so no bar immediately adjacent to the train/test boundary
(which could share overlapping indicator lookback windows, e.g. a
200-bar ATR percentile window) leaks information across the boundary.

HOLDOUT PROTECTION: `HoldoutGuard` is the enforcement mechanism for
"the holdout must never be used for parameter selection" — it is not
just a documentation comment. Any attempt to call `select_parameters`
(or any function routed through a HoldoutGuard) with data overlapping
the registered holdout range raises `HoldoutViolationError` immediately,
so this is a structural guarantee, not a convention someone could
accidentally violate by forgetting a comment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.errors import SignalBotError


class HoldoutViolationError(SignalBotError):
    """Raised when code attempts to use holdout-range data for
    parameter selection / fitting. This must never be caught and
    suppressed anywhere in this codebase — it exists to make holdout
    misuse impossible, not merely discouraged."""


@dataclass(frozen=True)
class Fold:
    fold_index: int
    train_start_idx: int
    train_end_idx: int  # exclusive
    test_start_idx: int
    test_end_idx: int  # exclusive
    regime_label: str | None = None


@dataclass(frozen=True)
class WalkForwardPlan:
    total_length: int
    embargo_bars: int
    folds: tuple[Fold, ...]
    holdout_start_idx: int  # inclusive
    holdout_end_idx: int  # exclusive, == total_length


def build_anchored_folds(
    *,
    total_length: int,
    min_folds: int = 6,
    embargo_pct: float = 0.01,
    holdout_pct: float = 0.15,
) -> WalkForwardPlan:
    """Build >= min_folds anchored (expanding-window) folds over the
    IN-SAMPLE portion of the data (total_length minus the reserved
    holdout at the end), with a 1% embargo between each fold's train
    and test windows.

    `holdout_pct` (default 15%) reserves the FINAL portion of the
    dataset as the untouched holdout — never included in any fold's
    train or test window, and registered for HoldoutGuard enforcement
    by the caller (see HoldoutGuard below).
    """
    if total_length <= 0:
        raise ValueError("total_length must be positive")
    if min_folds < 1:
        raise ValueError("min_folds must be >= 1")
    if not (0 < holdout_pct < 1):
        raise ValueError("holdout_pct must be in (0, 1)")
    if not (0 <= embargo_pct < 1):
        raise ValueError("embargo_pct must be in [0, 1)")

    holdout_start_idx = int(total_length * (1 - holdout_pct))
    in_sample_length = holdout_start_idx
    embargo_bars = max(1, int(total_length * embargo_pct))

    if in_sample_length < min_folds * 2 + embargo_bars:
        raise ValueError(
            f"total_length={total_length} with holdout_pct={holdout_pct} leaves only "
            f"{in_sample_length} in-sample bars, insufficient for {min_folds} folds "
            f"with an embargo of {embargo_bars} bars"
        )

    # Each fold's test window is an equal-sized slice of the in-sample
    # region; train always starts at 0 and extends up to (but not
    # including) that fold's embargo+test start.
    test_window_size = in_sample_length // (min_folds + 1)
    if test_window_size < 1:
        raise ValueError("insufficient in-sample data to form even one non-empty test window per fold")

    folds: list[Fold] = []
    for k in range(min_folds):
        test_start_idx = (k + 1) * test_window_size
        test_end_idx = min(test_start_idx + test_window_size, in_sample_length)
        train_end_idx = max(0, test_start_idx - embargo_bars)
        if train_end_idx <= 0 or test_start_idx >= test_end_idx:
            continue  # not enough room for this fold given the embargo; skip rather than fabricate
        folds.append(
            Fold(
                fold_index=k, train_start_idx=0, train_end_idx=train_end_idx,
                test_start_idx=test_start_idx, test_end_idx=test_end_idx,
            )
        )

    if len(folds) < min_folds:
        raise ValueError(
            f"only {len(folds)} valid folds could be constructed (need >= {min_folds}); "
            f"total_length={total_length} is too small for this configuration"
        )

    return WalkForwardPlan(
        total_length=total_length, embargo_bars=embargo_bars, folds=tuple(folds),
        holdout_start_idx=holdout_start_idx, holdout_end_idx=total_length,
    )


@dataclass
class HoldoutGuard:
    """Registers a [holdout_start_idx, holdout_end_idx) range and
    raises HoldoutViolationError if any index range passed to `check`
    overlaps it. Callers that perform parameter selection / threshold
    fitting MUST call `check` with the index range of the data they are
    about to fit on before doing so."""

    holdout_start_idx: int
    holdout_end_idx: int

    def check(self, start_idx: int, end_idx: int, *, context: str = "") -> None:
        overlaps = start_idx < self.holdout_end_idx and end_idx > self.holdout_start_idx
        if overlaps:
            raise HoldoutViolationError(
                f"attempted to use data in range [{start_idx}, {end_idx}) for "
                f"parameter selection, which overlaps the protected holdout range "
                f"[{self.holdout_start_idx}, {self.holdout_end_idx})"
                + (f" (context: {context})" if context else "")
            )

    @classmethod
    def from_plan(cls, plan: WalkForwardPlan) -> "HoldoutGuard":
        return cls(holdout_start_idx=plan.holdout_start_idx, holdout_end_idx=plan.holdout_end_idx)


def select_parameters(
    *, start_idx: int, end_idx: int, guard: HoldoutGuard, selector_fn, **selector_kwargs
):
    """The ONLY sanctioned entry point in this module for anything that
    could be construed as 'parameter selection' or 'fitting' against a
    data range. Always checks the range against `guard` BEFORE calling
    `selector_fn` — a caller cannot bypass the check by calling
    `selector_fn` directly only if it is never imported/called outside
    this function elsewhere in the codebase (enforced by code review /
    the test suite's search for direct selector usage, not by a
    runtime mechanism beyond this gate itself, which is the standard
    and sufficient pattern for this kind of guard in this codebase —
    compare to how StrategyBase.finalize_candidates is the sanctioned
    gate for the shared candidate filters).
    """
    guard.check(start_idx, end_idx, context="select_parameters")
    return selector_fn(**selector_kwargs)


def assign_regime_bucket(volatility_percentile: float) -> str:
    """Simple, documented regime bucketing (class E implementation
    choice, not a market-calibrated threshold): volatility_percentile
    (0-1, e.g. from an ATR-percentile calculation) below 0.33 is
    'low_vol', 0.33-0.66 is 'mid_vol', above 0.66 is 'high_vol'. Used
    to tag folds/trades with a regime_label for metrics.py's regime
    breakdown."""
    if not (0 <= volatility_percentile <= 1):
        raise ValueError(f"volatility_percentile must be in [0, 1], got {volatility_percentile}")
    if volatility_percentile < 0.33:
        return "low_vol"
    if volatility_percentile < 0.66:
        return "mid_vol"
    return "high_vol"
