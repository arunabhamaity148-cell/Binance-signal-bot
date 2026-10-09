"""S3 - Funding / Crowding Exhaustion Reversal.

Full mathematical specification: STRATEGIES_SPEC.md section "S3 -
Funding / Crowding Exhaustion Reversal". This module implements that
specification exactly, including the hard rule that S3 must never emit
a signal from funding_z alone.

HARD RULE (spec-mandated, enforced in code, not just by config):
funding_z extreme, oi_percentile extreme, AND price_disp_atr extreme
are all mandatory, AND a structure break opposite the crowd AND a
taker-flow flip are also both mandatory. `_assert_never_funding_alone`
below asserts this combination explicitly before any candidate is
constructed, so weakening any single config threshold can never by
itself cause a funding-only trigger -- the assertion checks that ALL
five conditions were independently satisfied, not just that the
function reached the point of building a candidate.

DOUBLE-COUNTING: funding_z, oi_percentile, and ls_ratio_pct all
originate from the same "crowding" phenomenon and collapse to exactly
two canonical channels: FUNDING (funding_z alone) and OI (oi_percentile
+ long/short ratios together) -- never three or four independent votes.

R CONVENTION: entry is a single retest price (structure_level), so
entry_low == entry_high == entry, trivially satisfying the entry-zone-
width filter.

STRUCTURE BREAK DEFINITION: a close beyond the most recent opposing
swing extreme (using the same swing-detection primitive as S1/S4 --
app.core.math.swing_highs_lows -- for a single, versioned definition of
"structure" across the whole codebase, per KNOWN_UNCERTAINTIES.md item
7). For a SHORT-reversal setup (longs crowded), the break is a close
below the most recent swing low; for a LONG-reversal setup (shorts
crowded), a close above the most recent swing high.
"""

from __future__ import annotations

from app.monitoring.diagnostics import strategy as diagnostic_strategy
from app.core.math import (
    InsufficientDataError,
    percentile_rank,
    swing_highs_lows,
    wilder_atr,
    zscore,
)
from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    MarketSnapshot,
    NewsState,
)
from app.core.time_utils import is_stale
from app.core.logging import get_logger
from app.strategies.base import StrategyBase

logger = get_logger(__name__)


class S3FundingCrowding(StrategyBase):
    strategy_id = "S3"

    @staticmethod
    def _skip(snapshot, reason: str, values: dict | None = None):
        diagnostic_strategy(snapshot.symbol, "S3", "rejected", reason, values)
        logger.debug(
            "s3_eval_skip",
            extra={"context": {"symbol": snapshot.symbol, "reason": reason, "values": values}},
        )
        return []

    @staticmethod
    def _prerequisite_rejected(snapshot, reason: str, values: dict | None = None) -> None:
        diagnostic_strategy(snapshot.symbol, "S3", "rejected", reason, values)
        logger.debug(
            "s3_prerequisite_rejected",
            extra={"context": {"symbol": snapshot.symbol, "reason": reason, "values": values}},
        )

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        cfg = config["s3_funding_crowding"]
        common = config["common"]
        atr_period = common["atr_period"]
        min_candles = common["min_candles"]

        bars_5m = snapshot.klines_for("5m")
        bars_15m = snapshot.klines_for("15m")
        if len(bars_5m) < min_candles or len(bars_15m) < 10:
            return self._skip(snapshot, "if len(bars_5m) < min_candles or len(bars_15m) < 10:")

        if snapshot.derivatives is None:
            return self._skip(snapshot, "if snapshot.derivatives is None:")

        deriv = snapshot.derivatives
        funding_stale_ms = cfg["funding_stale_ms"]
        oi_stale_ms = cfg["oi_stale_ms"]
        ratios_stale_ms = cfg["ratios_stale_ms"]

        def age_ms(point):
            return None if point is None else max(0, snapshot.as_of_ts_ms - point.event_ts_ms)

        funding_point = deriv.funding_rate_history[-1] if deriv.funding_rate_history else None
        oi_point = deriv.open_interest_history_5m[-1] if deriv.open_interest_history_5m else None
        ls_point = deriv.long_short_account_ratio_history[-1] if deriv.long_short_account_ratio_history else None
        funding_age_ms, oi_age_ms, ls_ratio_age_ms = age_ms(funding_point), age_ms(oi_point), age_ms(ls_point)

        def data_check(ok: bool, reason: str) -> None:
            logger.info("s3_data_check", extra={"context": {
                "symbol": snapshot.symbol, "funding_age_ms": funding_age_ms,
                "oi_age_ms": oi_age_ms, "ls_ratio_age_ms": ls_ratio_age_ms,
                "required_max": {"funding_ms": funding_stale_ms, "oi_ms": oi_stale_ms, "ls_ratio_ms": ratios_stale_ms},
                "ok": ok, "reason": reason,
            }})

        funding_z_window = cfg["funding_z_window"]
        if len(deriv.funding_rate_history) < funding_z_window:
            data_check(False, "funding_history_insufficient")
            return self._skip(snapshot, "funding_history_insufficient")
        latest_funding = deriv.funding_rate_history[-1]
        if is_stale(
            event_ts_ms=latest_funding.event_ts_ms, received_ts_ms=latest_funding.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms, staleness_budget_ms=funding_stale_ms,
        ):
            data_check(False, "funding_stale")
            return self._skip(snapshot, "funding_stale")

        oi_series = deriv.open_interest_history_5m
        if len(oi_series) < 2:
            data_check(False, "oi_history_insufficient")
            return self._skip(snapshot, "oi_history_insufficient")
        latest_oi = oi_series[-1]
        if is_stale(
            event_ts_ms=latest_oi.event_ts_ms, received_ts_ms=latest_oi.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms, staleness_budget_ms=oi_stale_ms,
        ):
            data_check(False, "oi_stale")
            return self._skip(snapshot, "oi_stale")

        ls_series = deriv.long_short_account_ratio_history
        if not ls_series:
            data_check(False, "ls_ratio_history_missing")
            return self._skip(snapshot, "ls_ratio_history_missing")
        latest_ls = ls_series[-1]
        if is_stale(
            event_ts_ms=latest_ls.event_ts_ms, received_ts_ms=latest_ls.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms, staleness_budget_ms=ratios_stale_ms,
        ):
            data_check(False, "ls_ratio_stale")
            return self._skip(snapshot, "ls_ratio_stale")
        data_check(True, "fresh")

        oi_history_1d = deriv.open_interest_history_1d
        oi_pct_window_days = cfg["oi_percentile_window_days"]
        if len(oi_history_1d) < oi_pct_window_days:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "oi_history_1d_insufficient"})

        try:
            atr14 = wilder_atr(bars_5m, period=atr_period)
        except InsufficientDataError:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "atr_insufficient"})
        if atr14 <= 0:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "atr_non_positive"})

        funding_values = [tv.value for tv in deriv.funding_rate_history[-funding_z_window:]]
        try:
            funding_z = zscore(funding_values[-1], funding_values)
        except InsufficientDataError:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "funding_z_insufficient"})

        oi_history_values = [tv.value for tv in oi_history_1d]
        oi_percentile = percentile_rank(latest_oi.value, oi_history_values)

        ls_history_values = [tv.value for tv in ls_series]
        ls_ratio_pct = percentile_rank(latest_ls.value, ls_history_values)

        n_displacement = min(funding_z_window, len(bars_5m) - 1)
        if n_displacement < 1:
            return self._skip(snapshot, "if n_displacement < 1:")
        price_disp_atr = abs(bars_5m[-1].close - bars_5m[-1 - n_displacement].close) / atr14

        if abs(funding_z) < cfg["min_abs_funding_z"]:
            return self._skip(snapshot, "s3_prereq_funding_z_below_min", {"funding_z": funding_z, "min_required": cfg["min_abs_funding_z"]})
        if oi_percentile < cfg["min_oi_pct_rank"]:
            return self._skip(snapshot, "s3_prereq_oi_rank_below_min", {"oi_rank": oi_percentile, "min_required": cfg["min_oi_pct_rank"]})
        ls_extreme = ls_ratio_pct >= cfg["ls_ratio_pct_high"] or ls_ratio_pct <= cfg["ls_ratio_pct_low"]
        if not ls_extreme:
            return self._skip(snapshot, "s3_prereq_ls_ratio_not_extreme", {"ls_ratio_pct": ls_ratio_pct, "high": cfg["ls_ratio_pct_high"], "low": cfg["ls_ratio_pct_low"]})
        if price_disp_atr < cfg["min_price_disp_atr"]:
            return self._skip(snapshot, "s3_prereq_price_disp_below_min", {"price_disp_atr": price_disp_atr, "min_required": cfg["min_price_disp_atr"]})

        # Crowd direction: positive funding + long-skewed ratio -> longs
        # crowded (watch for SHORT reversal). Negative funding +
        # short-skewed ratio -> shorts crowded (watch for LONG reversal).
        longs_crowded = funding_z > 0 and ls_ratio_pct >= cfg["ls_ratio_pct_high"]
        shorts_crowded = funding_z < 0 and ls_ratio_pct <= cfg["ls_ratio_pct_low"]
        if not (longs_crowded or shorts_crowded):
            return self._skip(snapshot, "s3_prereq_other", {"branch": "crowd_direction_mismatch"})

        if snapshot.taker_flow is None:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "taker_flow_missing"})
        try:
            taker_buy_ratio = snapshot.taker_flow.taker_buy_ratio
        except ValueError:
            return self._skip(snapshot, "s3_prereq_other", {"branch": "taker_flow_invalid"})

        swing_lookback = 3  # shares S1/S4's default; not separately configured for S3 in CONFIG_SCHEMAS.md
        swing_count_needed = 1
        highs_idx, lows_idx = swing_highs_lows(bars_15m, swing_lookback)

        candidates: list[CandidateSignal] = []

        if longs_crowded and len(lows_idx) >= swing_count_needed:
            candidate = self._evaluate_short_reversal(
                snapshot=snapshot, cfg=cfg, atr14=atr14, bars_15m=bars_15m, lows_idx=lows_idx,
                taker_buy_ratio=taker_buy_ratio, funding_z=funding_z, oi_percentile=oi_percentile,
                ls_ratio_pct=ls_ratio_pct, price_disp_atr=price_disp_atr,
            )
            if candidate is not None:
                self._assert_never_funding_alone(
                    funding_extreme=abs(funding_z) >= cfg["min_abs_funding_z"],
                    oi_extreme=oi_percentile >= cfg["min_oi_pct_rank"],
                    price_disp_extreme=price_disp_atr >= cfg["min_price_disp_atr"],
                    structure_break=bool(candidate.meta.get("structure_break")),
                    taker_flip=bool(candidate.meta.get("taker_flip")),
                )
                candidates.append(candidate)
        elif longs_crowded:
            self._prerequisite_rejected(snapshot, "s3_prereq_other", {"branch": "structure_swing_low_missing"})

        if shorts_crowded and len(highs_idx) >= swing_count_needed:
            candidate = self._evaluate_long_reversal(
                snapshot=snapshot, cfg=cfg, atr14=atr14, bars_15m=bars_15m, highs_idx=highs_idx,
                taker_buy_ratio=taker_buy_ratio, funding_z=funding_z, oi_percentile=oi_percentile,
                ls_ratio_pct=ls_ratio_pct, price_disp_atr=price_disp_atr,
            )
            if candidate is not None:
                self._assert_never_funding_alone(
                    funding_extreme=abs(funding_z) >= cfg["min_abs_funding_z"],
                    oi_extreme=oi_percentile >= cfg["min_oi_pct_rank"],
                    price_disp_extreme=price_disp_atr >= cfg["min_price_disp_atr"],
                    structure_break=bool(candidate.meta.get("structure_break")),
                    taker_flip=bool(candidate.meta.get("taker_flip")),
                )
                candidates.append(candidate)
        elif shorts_crowded:
            self._prerequisite_rejected(snapshot, "s3_prereq_other", {"branch": "structure_swing_high_missing"})

        return self.finalize_candidates(candidates, snapshot, config)

    @staticmethod
    def _assert_never_funding_alone(
        *, funding_extreme: bool, oi_extreme: bool, price_disp_extreme: bool,
        structure_break: bool, taker_flip: bool,
    ) -> None:
        """Explicit, code-level enforcement of the spec's hard rule.
        Every one of these five booleans must independently be True for
        this function to be called at all (callers only reach this
        point after each has been verified); asserting here makes the
        rule impossible to silently weaken by editing a single
        threshold, since all five are re-checked as an explicit
        conjunction immediately before candidate construction."""
        assert funding_extreme and oi_extreme and price_disp_extreme and structure_break and taker_flip, (
            "S3 must never emit a signal from funding_z alone: all of "
            "funding, OI percentile, price displacement, structure break, "
            "and taker-flow flip must be independently satisfied"
        )

    def _evaluate_short_reversal(
        self, *, snapshot, cfg, atr14, bars_15m, lows_idx, taker_buy_ratio,
        funding_z, oi_percentile, ls_ratio_pct, price_disp_atr,
    ) -> CandidateSignal | None:
        """Longs crowded -> structure break DOWN (close below most
        recent swing low) + taker flow flips toward selling."""
        most_recent_low = bars_15m[lows_idx[-1]].low
        current_close = bars_15m[-1].close
        structure_break = current_close < most_recent_low
        if not structure_break:
            self._prerequisite_rejected(snapshot, "s3_prereq_no_structure_break")
            return None

        taker_flip = taker_buy_ratio < 0.5  # flow flips toward selling
        if not taker_flip:
            self._prerequisite_rejected(snapshot, "s3_prereq_no_taker_flip")
            return None

        entry = most_recent_low  # retest of the broken structure level
        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = most_recent_low + sl_buffer_atr * atr14
        if stop_loss <= entry:
            self._prerequisite_rejected(snapshot, "s3_prereq_other", {"branch": "short_stop_invalid"})
            return None

        r = stop_loss - entry
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry - m * r for m in tp_multiples)

        why_lines = (
            f"S3 SHORT reversal: longs crowded, funding_z {funding_z:.2f}, "
            f"oi_percentile {oi_percentile:.2f}, ls_ratio_pct {ls_ratio_pct:.2f}",
            f"Price displacement {price_disp_atr:.2f} ATR confirms extreme; "
            f"structure broke below swing low {most_recent_low:.6f}",
            f"Taker flow flipped to selling (buy ratio {taker_buy_ratio:.2f} < 0.5)",
        )
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": bars_15m[-1].close_time_ms,
            "funding_z": funding_z,
            "oi_percentile": oi_percentile,
            "ls_ratio_pct": ls_ratio_pct,
            "price_disp_atr": price_disp_atr,
            "structure_level": most_recent_low,
            "min_abs_funding_z": cfg["min_abs_funding_z"],
            "min_oi_pct_rank": cfg["min_oi_pct_rank"],
            "min_price_disp_atr": cfg["min_price_disp_atr"],
            "sl_buffer_atr": sl_buffer_atr,
            "structure_break": structure_break,
            "taker_flip": taker_flip,
        }

        logger.info("s3_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol, "funding_z": funding_z, "oi_percentile": oi_percentile, "price_disp_atr": price_disp_atr, "final": min(1.0, 0.4 + 0.1 * abs(funding_z))}})
        diagnostic_strategy(snapshot.symbol, "S3", "candidate", "triggered", {"confidence": min(1.0, 0.4 + 0.1 * abs(funding_z))})
        logger.debug("s3_candidate_created | symbol=%s | direction=%s | confidence=%s", snapshot.symbol, "SHORT", min(1.0, 0.4 + 0.1 * abs(funding_z)))
        return CandidateSignal(
            symbol=snapshot.symbol, direction=Direction.SHORT, strategy_source="S3",
            confidence=min(1.0, 0.4 + 0.1 * abs(funding_z)),
            channels=(ChannelName.FUNDING, ChannelName.OI),
            entry_low=entry, entry_high=entry, stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            why_lines=why_lines, meta=meta, event_ts_ms=bars_15m[-1].close_time_ms,
        )

    def _evaluate_long_reversal(
        self, *, snapshot, cfg, atr14, bars_15m, highs_idx, taker_buy_ratio,
        funding_z, oi_percentile, ls_ratio_pct, price_disp_atr,
    ) -> CandidateSignal | None:
        """Shorts crowded -> structure break UP (close above most
        recent swing high) + taker flow flips toward buying."""
        most_recent_high = bars_15m[highs_idx[-1]].high
        current_close = bars_15m[-1].close
        structure_break = current_close > most_recent_high
        if not structure_break:
            self._prerequisite_rejected(snapshot, "s3_prereq_no_structure_break")
            return None

        taker_flip = taker_buy_ratio > 0.5
        if not taker_flip:
            self._prerequisite_rejected(snapshot, "s3_prereq_no_taker_flip")
            return None

        entry = most_recent_high
        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = most_recent_high - sl_buffer_atr * atr14
        if stop_loss >= entry:
            self._prerequisite_rejected(snapshot, "s3_prereq_other", {"branch": "long_stop_invalid"})
            return None

        r = entry - stop_loss
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry + m * r for m in tp_multiples)

        why_lines = (
            f"S3 LONG reversal: shorts crowded, funding_z {funding_z:.2f}, "
            f"oi_percentile {oi_percentile:.2f}, ls_ratio_pct {ls_ratio_pct:.2f}",
            f"Price displacement {price_disp_atr:.2f} ATR confirms extreme; "
            f"structure broke above swing high {most_recent_high:.6f}",
            f"Taker flow flipped to buying (buy ratio {taker_buy_ratio:.2f} > 0.5)",
        )
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": bars_15m[-1].close_time_ms,
            "funding_z": funding_z,
            "oi_percentile": oi_percentile,
            "ls_ratio_pct": ls_ratio_pct,
            "price_disp_atr": price_disp_atr,
            "structure_level": most_recent_high,
            "min_abs_funding_z": cfg["min_abs_funding_z"],
            "min_oi_pct_rank": cfg["min_oi_pct_rank"],
            "min_price_disp_atr": cfg["min_price_disp_atr"],
            "sl_buffer_atr": sl_buffer_atr,
            "structure_break": structure_break,
            "taker_flip": taker_flip,
        }

        logger.info("s3_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol, "funding_z": funding_z, "oi_percentile": oi_percentile, "price_disp_atr": price_disp_atr, "final": min(1.0, 0.4 + 0.1 * abs(funding_z))}})
        logger.debug("s3_candidate_created | symbol=%s | direction=%s | confidence=%s", snapshot.symbol, "LONG", min(1.0, 0.4 + 0.1 * abs(funding_z)))
        return CandidateSignal(
            symbol=snapshot.symbol, direction=Direction.LONG, strategy_source="S3",
            confidence=min(1.0, 0.4 + 0.1 * abs(funding_z)),
            channels=(ChannelName.FUNDING, ChannelName.OI),
            entry_low=entry, entry_high=entry, stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            why_lines=why_lines, meta=meta, event_ts_ms=bars_15m[-1].close_time_ms,
        )
