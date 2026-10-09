"""S4 - OI-Confirmed Trend Continuation.

Full mathematical specification: STRATEGIES_SPEC.md section "S4 -
OI-Confirmed Trend Continuation". This module implements that
specification exactly, including the explicit guard against
triggering on EMA alignment alone (pullback + structure break are
both mandatory).
"""

from __future__ import annotations

from app.monitoring.diagnostics import strategy as diagnostic_strategy
from app.core.math import (
    InsufficientDataError,
    ema_series,
    has_hh_hl_sequence,
    has_lh_ll_sequence,
    wilder_atr,
    wilder_atr_series,
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
from app.data.derivatives import oi_series_for_window
from app.strategies.base import StrategyBase

logger = get_logger(__name__)


class S4OiTrend(StrategyBase):
    strategy_id = "S4"

    @staticmethod
    def _skip(snapshot, reason: str):
        diagnostic_strategy(snapshot.symbol, "S4", "rejected", reason)
        logger.debug(
            "s4_eval_skip",
            extra={"context": {"symbol": snapshot.symbol, "reason": reason}},
        )
        return []

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        cfg = config["s4_oi_trend"]
        common = config["common"]
        atr_period = common["atr_period"]
        min_candles = common["min_candles"]

        bars_5m = snapshot.klines_for("5m")
        bars_15m = snapshot.klines_for("15m")
        bars_1h = snapshot.klines_for("1h")
        bars_4h = snapshot.klines_for("4h")

        if len(bars_5m) < min_candles:
            return self._skip(snapshot, "if len(bars_5m) < min_candles:")

        ema_fast_period = cfg["ema_fast_period"]
        ema_slow_period = cfg["ema_slow_period"]
        ema_slope_lookback = cfg["ema_slope_lookback"]

        required_1h = ema_slow_period + ema_slope_lookback + 1
        if len(bars_1h) < required_1h:
            return self._skip(snapshot, "if len(bars_1h) < required_1h:")
        if len(bars_4h) < ema_slow_period:
            return self._skip(snapshot, "if len(bars_4h) < ema_slow_period:")
        if len(bars_15m) < cfg["structure_swing_count"] * 2 + cfg["structure_swing_lookback"] * 2 + 5:
            return self._skip(snapshot, "if len(bars_15m) < cfg['structure_swing_count'] * 2 + cfg['structure_swing_lookback'] * 2 + 5:")

        closes_1h = [b.close for b in bars_1h]
        closes_4h = [b.close for b in bars_4h]

        try:
            atr14_1h = wilder_atr(bars_1h, period=atr_period)
            ema_fast_series_1h = ema_series(closes_1h, ema_fast_period)
            ema_slow_series_1h = ema_series(closes_1h, ema_slow_period)
            ema_fast_4h = ema_series(closes_4h, ema_fast_period)[-1]
            ema_slow_4h = ema_series(closes_4h, ema_slow_period)[-1]
            atr14_5m = wilder_atr(bars_5m, period=atr_period)
        except InsufficientDataError:
            return self._skip(snapshot, "except InsufficientDataError:")

        if atr14_1h <= 0 or atr14_5m <= 0:
            return self._skip(snapshot, "if atr14_1h <= 0 or atr14_5m <= 0:")

        ema_fast_now = ema_fast_series_1h[-1]
        ema_slow_now = ema_slow_series_1h[-1]

        slope_lookback_idx = -(ema_slope_lookback + 1)
        if abs(slope_lookback_idx) > len(ema_fast_series_1h):
            return self._skip(snapshot, "if abs(slope_lookback_idx) > len(ema_fast_series_1h):")
        ema_fast_prior = ema_fast_series_1h[slope_lookback_idx]
        ema_slope_fast = (ema_fast_now - ema_fast_prior) / atr14_1h

        # OI data required and must be fresh.
        if snapshot.derivatives is None:
            return self._skip(snapshot, "if snapshot.derivatives is None:")
        oi_stale_ms = cfg["oi_stale_ms"]
        oi_series_5m = snapshot.derivatives.open_interest_history_5m
        if not oi_series_5m:
            return self._skip(snapshot, "if not oi_series_5m:")
        latest_oi_point = oi_series_5m[-1]
        if is_stale(
            event_ts_ms=latest_oi_point.event_ts_ms,
            received_ts_ms=latest_oi_point.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms,
            staleness_budget_ms=oi_stale_ms,
        ):
            return self._skip(snapshot, "):")

        oi_window_bars = cfg["oi_window_bars"]
        oi_values = oi_series_for_window(oi_series_5m, snapshot.as_of_ts_ms, oi_stale_ms * 50)
        if len(oi_values) < oi_window_bars + 1:
            return self._skip(snapshot, "if len(oi_values) < oi_window_bars + 1:")
        oi_now = oi_values[-1]
        oi_prior = oi_values[-(oi_window_bars + 1)]
        if oi_prior == 0:
            return self._skip(snapshot, "if oi_prior == 0:")
        oi_delta_pct = (oi_now - oi_prior) / oi_prior * 100

        candidates: list[CandidateSignal] = []

        long_trend = ema_fast_now > ema_slow_now and ema_slope_fast > 0
        short_trend = ema_fast_now < ema_slow_now and ema_slope_fast < 0

        if long_trend:
            htf_aligned = ema_fast_4h > ema_slow_4h
            structure_ok = has_hh_hl_sequence(
                bars_15m, cfg["structure_swing_lookback"], cfg["structure_swing_count"]
            )
            price_now = bars_5m[-1].close
            price_prior = bars_5m[-(oi_window_bars + 1)].close if len(bars_5m) > oi_window_bars else None
            price_rising = price_prior is not None and price_now > price_prior
            oi_rising = oi_delta_pct >= cfg["min_oi_chg_pct"]

            pullback_ok = self._check_pullback(
                bars_5m, ema_fast_now, atr14_5m, cfg["pullback_tolerance_atr"], cfg["pullback_lookback_bars"]
            )

            if htf_aligned and structure_ok and pullback_ok and price_rising and oi_rising:
                candidate = self._build_candidate(
                    snapshot=snapshot,
                    cfg=cfg,
                    direction=Direction.LONG,
                    ema_fast=ema_fast_now,
                    atr14_1h=atr14_1h,
                    oi_delta_pct=oi_delta_pct,
                    ema_slope_fast=ema_slope_fast,
                    current_bar=bars_5m[-1],
                )
                if candidate is not None:
                    candidates.append(candidate)

        if short_trend:
            htf_aligned = ema_fast_4h < ema_slow_4h
            structure_ok = has_lh_ll_sequence(
                bars_15m, cfg["structure_swing_lookback"], cfg["structure_swing_count"]
            )
            price_now = bars_5m[-1].close
            price_prior = bars_5m[-(oi_window_bars + 1)].close if len(bars_5m) > oi_window_bars else None
            price_falling = price_prior is not None and price_now < price_prior
            oi_rising = oi_delta_pct >= cfg["min_oi_chg_pct"]

            pullback_ok = self._check_pullback(
                bars_5m, ema_fast_now, atr14_5m, cfg["pullback_tolerance_atr"], cfg["pullback_lookback_bars"]
            )

            if htf_aligned and structure_ok and pullback_ok and price_falling and oi_rising:
                candidate = self._build_candidate(
                    snapshot=snapshot,
                    cfg=cfg,
                    direction=Direction.SHORT,
                    ema_fast=ema_fast_now,
                    atr14_1h=atr14_1h,
                    oi_delta_pct=oi_delta_pct,
                    ema_slope_fast=ema_slope_fast,
                    current_bar=bars_5m[-1],
                )
                if candidate is not None:
                    candidates.append(candidate)

        if not candidates:
            diagnostic_strategy(snapshot.symbol, "S4", "rejected", "no_valid_trend_setup", {
                "long_trend": long_trend, "short_trend": short_trend,
                "oi_delta_pct": oi_delta_pct,
            })
        else:
            diagnostic_strategy(snapshot.symbol, "S4", "candidate", "trend_setup_valid", {"count": len(candidates)})
        return self.finalize_candidates(candidates, snapshot, config)

    def _check_pullback(
        self, bars_5m, ema_fast, atr14_5m, tolerance_atr, lookback_bars
    ) -> bool:
        """Pullback observed within the last `lookback_bars` bars: a
        close within `tolerance_atr` ATR of ema_fast. This runs on the
        5m series as the finest-grained proxy for 'recent price
        approached the 1H EMA' without needing a resampled 1h-aligned
        pullback series."""
        if len(bars_5m) < lookback_bars:
            return False
        recent = bars_5m[-lookback_bars:]
        return any(abs(b.close - ema_fast) <= tolerance_atr * atr14_5m for b in recent)

    def _build_candidate(
        self, *, snapshot, cfg, direction, ema_fast, atr14_1h, oi_delta_pct, ema_slope_fast, current_bar
    ) -> CandidateSignal | None:
        sl_stop_atr_mult = cfg["sl_stop_atr_mult"]
        sl_buffer_atr = cfg["sl_buffer_atr"]
        total_stop_atr = sl_stop_atr_mult + sl_buffer_atr

        entry_mid = current_bar.close
        if direction == Direction.LONG:
            stop_loss = ema_fast - total_stop_atr * atr14_1h
            if stop_loss >= entry_mid:
                return None
            r = entry_mid - stop_loss
            tp_multiples = cfg["tp_r_multiples"]
            tp1, tp2, tp3, tp4 = (entry_mid + m * r for m in tp_multiples)
        else:
            stop_loss = ema_fast + total_stop_atr * atr14_1h
            if stop_loss <= entry_mid:
                return None
            r = stop_loss - entry_mid
            tp_multiples = cfg["tp_r_multiples"]
            tp1, tp2, tp3, tp4 = (entry_mid - m * r for m in tp_multiples)

        why_lines = (
            f"S4 {direction.value} trend: EMA{cfg['ema_fast_period']}/"
            f"{cfg['ema_slow_period']} aligned, slope {ema_slope_fast:.3f} ATR over {cfg['ema_slope_lookback']} bars",
            f"HTF (4H) trend confirms direction; 15m structure confirms "
            f"{'HH/HL' if direction == Direction.LONG else 'LH/LL'}",
            f"OI expansion {oi_delta_pct:.2f}% confirms continuation "
            f"(threshold {cfg['min_oi_chg_pct']}%)",
        )

        meta = {
            "atr14": atr14_1h,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": current_bar.close_time_ms,
            "ema_fast": ema_fast,
            "ema_slope_fast": ema_slope_fast,
            "oi_delta_pct": oi_delta_pct,
            "sl_stop_atr_mult": sl_stop_atr_mult,
            "sl_buffer_atr": sl_buffer_atr,
            "min_oi_chg_pct": cfg["min_oi_chg_pct"],
            "pullback_tolerance_atr": cfg["pullback_tolerance_atr"],
        }

        confidence = min(1.0, 0.5 + abs(ema_slope_fast) * 0.1)
        logger.info("s4_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol,
            "ema_slope_atr": ema_slope_fast, "oi_delta_pct": oi_delta_pct,
            "final": confidence}})
        meta["confidence_breakdown"] = {"ema_slope_atr": ema_slope_fast,
                                         "oi_delta_pct": oi_delta_pct, "final": confidence}
        logger.debug("s4_candidate_created | symbol=%s | direction=%s | confidence=%s", snapshot.symbol, direction.value, confidence)
        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=direction,
            strategy_source="S4",
            confidence=confidence,
            channels=(ChannelName.PRICE_STRUCTURE, ChannelName.OI),
            entry_low=min(entry_mid, entry_mid),
            entry_high=max(entry_mid, entry_mid),
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            tp4=tp4,
            why_lines=why_lines,
            meta=meta,
            event_ts_ms=current_bar.close_time_ms,
        )
