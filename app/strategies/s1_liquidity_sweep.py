"""S1 - Liquidity Sweep & Reclaim.

Full mathematical specification: STRATEGIES_SPEC.md section "S1 -
Liquidity Sweep & Reclaim". This module implements that specification
exactly, including every fail-closed condition.
"""

from __future__ import annotations

from app.core.math import InsufficientDataError, wilder_atr
from app.core.logging import get_logger
from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    MarketSnapshot,
    NewsState,
)
from app.core.time_utils import is_stale
from app.monitoring.diagnostics import strategy as diagnostic_strategy
from app.strategies.base import StrategyBase

logger = get_logger(__name__)


def threshold_factor(actual: float, threshold: float) -> float:
    """Scale positive evidence smoothly from zero to full credit.

    Values on the wrong side of a threshold remain fail-closed. Positive
    evidence earns proportional credit across ``[0, 2 * threshold]``.
    """
    if threshold <= 0 or actual <= 0:
        return 0.0
    return min(1.0, actual / (2.0 * threshold))


class S1LiquiditySweep(StrategyBase):
    strategy_id = "S1"

    @staticmethod
    def _confidence_breakdown(*, direction: Direction, base_confidence: float,
                              taker_buy_ratio: float, sweep_distance: float,
                              reclaim_distance: float, reclaim_band_atr: float,
                              penetration_min_atr: float, volume_ratio: float) -> dict[str, float]:
        """Convert independent S1 setup quality signals into confidence."""
        if direction == Direction.LONG:
            taker_strength = taker_buy_ratio - 0.50
        else:
            taker_strength = 0.50 - taker_buy_ratio
        taker_factor = threshold_factor(taker_strength, 0.05)
        # Positive distance means price reclaimed deeper into the swept side;
        # negative distance is a wrong-side close and fails closed. The raw
        # reclaim gate and factor normalization use the same band.
        reclaim_threshold = max(reclaim_band_atr, 1e-9)
        reclaim_factor = threshold_factor(reclaim_distance, reclaim_threshold)
        sweep_factor = threshold_factor(sweep_distance, penetration_min_atr)
        volume_factor = threshold_factor(volume_ratio - 0.5, 0.5)
        quality = (0.35 * taker_factor + 0.30 * reclaim_factor +
                   0.25 * sweep_factor + 0.10 * volume_factor)
        # Keep base confidence as the floor and add a bounded quality
        # increment; the independent grade-B threshold remains 0.55.
        final = max(0.0, min(1.0, base_confidence + 0.10 * quality))
        return {"base": base_confidence, "taker_flow_factor": taker_factor,
                "reclaim_quality_factor": reclaim_factor, "sweep_distance_factor": sweep_factor,
                "volume_factor": volume_factor, "final": final}

    @staticmethod
    def _volume_ratio(taker_flow) -> float:
        values = [float(value) for value in taker_flow.total_volume_last_bars if float(value) > 0]
        return values[-1] / (sum(values) / len(values)) if values else 0.5

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        cfg = config["s1_liquidity_sweep"]
        common = config["common"]
        atr_period = common["atr_period"]
        min_candles = common["min_candles"]

        def reject(reason: str) -> list[CandidateSignal]:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", reason)
            return []

        bars_5m = snapshot.klines_for("5m")
        if len(bars_5m) < min_candles:
            return reject("insufficient_history")

        try:
            atr14 = wilder_atr(bars_5m, period=atr_period)
        except InsufficientDataError:
            return reject("atr_unavailable")

        if atr14 <= 0:
            return reject("degenerate_atr")

        # Taker-flow freshness and availability check.
        if snapshot.taker_flow is None:
            return reject("taker_flow_unavailable")
        if is_stale(
            event_ts_ms=snapshot.taker_flow.event_ts_ms,
            received_ts_ms=snapshot.taker_flow.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms,
            staleness_budget_ms=cfg["taker_stale_ms"],
        ):
            return reject("taker_flow_stale")

        try:
            taker_buy_ratio = snapshot.taker_flow.taker_buy_ratio
        except ValueError:
            return reject("taker_flow_volume_unavailable")

        swing_lookback = cfg["swing_lookback"]
        if len(bars_5m) < swing_lookback + 2:
            return reject("insufficient_history")

        i = len(bars_5m) - 1  # index of most recently closed bar
        window = bars_5m[i - swing_lookback : i]  # bars i-1 .. i-swing_lookback
        if not window:
            return reject("insufficient_history")

        l_high = max(b.high for b in window)
        l_low = min(b.low for b in window)

        current = bars_5m[i]
        prev = bars_5m[i - 1]

        candidates: list[CandidateSignal] = []

        long_candidate = self._evaluate_long(
            snapshot=snapshot,
            cfg=cfg,
            atr14=atr14,
            l_high=l_high,
            current=current,
            prev=prev,
            bars_5m=bars_5m,
            taker_buy_ratio=taker_buy_ratio,
        )
        if long_candidate is not None:
            candidates.append(long_candidate)

        short_candidate = self._evaluate_short(
            snapshot=snapshot,
            cfg=cfg,
            atr14=atr14,
            l_low=l_low,
            current=current,
            prev=prev,
            bars_5m=bars_5m,
            taker_buy_ratio=taker_buy_ratio,
        )
        if short_candidate is not None:
            candidates.append(short_candidate)

        if candidates:
            diagnostic_strategy(snapshot.symbol, "S1", "candidate", "sweep_setup_valid", {"count": len(candidates)})
        else:
            diagnostic_strategy(snapshot.symbol, "S1", "none", "no_valid_sweep_setup")
        return self.finalize_candidates(candidates, snapshot, config)

    def _evaluate_long(
        self, *, snapshot, cfg, atr14, l_high, current, prev, bars_5m, taker_buy_ratio
    ) -> CandidateSignal | None:
        sweep_dist_long = (current.high - l_high) / atr14
        if sweep_dist_long < cfg["penetration_min_atr"]:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_sweep_below_threshold")
            return None
        if not (current.high > l_high):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_sweep_not_above_level")
            return None
        if not (current.close < l_high):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_reclaim_not_below_level")
            return None
        reclaim_band_atr = cfg["reclaim_band_atr"]
        if not (current.close >= l_high - reclaim_band_atr * atr14):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_reclaim_outside_band")
            return None

        if taker_buy_ratio < cfg["taker_buy_min_long"]:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_taker_flow_below_threshold")
            return None

        entry_offset_atr = cfg["entry_offset_atr"]
        entry_low = l_high - entry_offset_atr * atr14
        entry_high = l_high + entry_offset_atr * atr14
        entry_mid = (entry_low + entry_high) / 2

        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = min(current.low, prev.low) - sl_buffer_atr * atr14

        if stop_loss >= entry_low:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "long_stop_geometry_invalid")
            return None  # degenerate geometry, refuse to emit

        # R is measured from the entry-zone MIDPOINT, not entry_low/high
        # (see StrategyBase docstring: this is the shared convention
        # every strategy uses, so TP_k is genuinely k*R as graded by
        # signal_engine's R:R calculation, which also uses the midpoint).
        r = entry_mid - stop_loss
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry_mid + m * r for m in tp_multiples)

        why_lines = (
            f"S1 LONG sweep: high {current.high:.6f} penetrated L_high "
            f"{l_high:.6f} by {sweep_dist_long:.2f} ATR",
            f"Reclaim confirmed: close {current.close:.6f} within "
            f"{reclaim_band_atr} ATR of L_high",
            f"Taker buy ratio {taker_buy_ratio:.2f} >= "
            f"{cfg['taker_buy_min_long']} threshold",
        )

        breakdown = self._confidence_breakdown(
            direction=Direction.LONG, base_confidence=cfg.get("base_confidence", 0.62),
            taker_buy_ratio=taker_buy_ratio, sweep_distance=sweep_dist_long,
            reclaim_distance=(l_high - current.close) / atr14, reclaim_band_atr=reclaim_band_atr,
            penetration_min_atr=cfg["penetration_min_atr"], volume_ratio=self._volume_ratio(snapshot.taker_flow),
        )
        reclaim_threshold = max(reclaim_band_atr, 1e-9)
        # Reclaim quality is normalized depth back inside the swept level.
        # A wrong-side close is negative and fails closed at factor stage.
        reclaim_raw = (l_high - current.close) / atr14
        reclaim_factor = breakdown["reclaim_quality_factor"]
        logger.info("s1_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol, **breakdown}})
        logger.debug(
            "s1_reclaim_debug | symbol=%s | close_price=%s | reclaim_level=%s | "
            "distance_atr=%s | threshold=%s | raw=%s | factor=%s",
            snapshot.symbol, current.close, l_high, (l_high - current.close) / atr14,
            reclaim_threshold, reclaim_raw, reclaim_factor,
        )
        if breakdown["final"] <= 0.0:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "zero_confidence_factor")
            zero_factors = [
                key for key, value in breakdown.items()
                if isinstance(value, (int, float)) and value == 0.0
            ]
            logger.debug(
                "s1_candidate_rejected | symbol=%s | direction=LONG | "
                "reason=zero_confidence | zero_factors=%s",
                snapshot.symbol, zero_factors,
            )
            return None
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": current.close_time_ms,
            "l_high": l_high,
            "sweep_dist_long": sweep_dist_long,
            "taker_buy_ratio": taker_buy_ratio,
            "penetration_min_atr": cfg["penetration_min_atr"],
            "reclaim_band_atr": reclaim_band_atr,
            "entry_offset_atr": entry_offset_atr,
            "sl_buffer_atr": sl_buffer_atr,
            "taker_buy_min_long": cfg["taker_buy_min_long"], "confidence_breakdown": breakdown,
        }

        diagnostic_strategy(snapshot.symbol, "S1", "candidate", "long_sweep_setup_valid", {"confidence": breakdown["final"]})
        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.LONG,
            strategy_source="S1",
            confidence=breakdown["final"],
            channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            tp4=tp4,
            why_lines=why_lines,
            meta=meta,
            event_ts_ms=current.close_time_ms,
        )

    def _evaluate_short(
        self, *, snapshot, cfg, atr14, l_low, current, prev, bars_5m, taker_buy_ratio
    ) -> CandidateSignal | None:
        sweep_dist_short = (l_low - current.low) / atr14
        if sweep_dist_short < cfg["penetration_min_atr"]:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_sweep_below_threshold")
            return None
        if not (current.low < l_low):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_sweep_not_below_level")
            return None
        if not (current.close > l_low):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_reclaim_not_above_level")
            return None
        reclaim_band_atr = cfg["reclaim_band_atr"]
        if not (current.close <= l_low + reclaim_band_atr * atr14):
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_reclaim_outside_band")
            return None

        if taker_buy_ratio > cfg["taker_sell_max_short"]:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_taker_flow_above_threshold")
            return None

        entry_offset_atr = cfg["entry_offset_atr"]
        entry_low = l_low - entry_offset_atr * atr14
        entry_high = l_low + entry_offset_atr * atr14
        entry_mid = (entry_low + entry_high) / 2

        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = max(current.high, prev.high) + sl_buffer_atr * atr14

        if stop_loss <= entry_high:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "short_stop_geometry_invalid")
            return None  # degenerate geometry

        # R from the entry-zone MIDPOINT (see LONG branch comment above
        # and StrategyBase docstring for the shared convention).
        r = stop_loss - entry_mid
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry_mid - m * r for m in tp_multiples)

        why_lines = (
            f"S1 SHORT sweep: low {current.low:.6f} penetrated L_low "
            f"{l_low:.6f} by {sweep_dist_short:.2f} ATR",
            f"Reclaim confirmed: close {current.close:.6f} within "
            f"{reclaim_band_atr} ATR of L_low",
            f"Taker sell dominance: buy ratio {taker_buy_ratio:.2f} <= "
            f"{cfg['taker_sell_max_short']} threshold",
        )

        breakdown = self._confidence_breakdown(
            direction=Direction.SHORT, base_confidence=cfg.get("base_confidence", 0.62),
            taker_buy_ratio=taker_buy_ratio, sweep_distance=sweep_dist_short,
            reclaim_distance=(current.close - l_low) / atr14, reclaim_band_atr=reclaim_band_atr,
            penetration_min_atr=cfg["penetration_min_atr"], volume_ratio=self._volume_ratio(snapshot.taker_flow),
        )
        reclaim_threshold = max(reclaim_band_atr, 1e-9)
        reclaim_raw = (current.close - l_low) / atr14
        reclaim_factor = breakdown["reclaim_quality_factor"]
        logger.info("s1_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol, **breakdown}})
        logger.debug(
            "s1_reclaim_debug | symbol=%s | close_price=%s | reclaim_level=%s | "
            "distance_atr=%s | threshold=%s | raw=%s | factor=%s",
            snapshot.symbol, current.close, l_low, (current.close - l_low) / atr14,
            reclaim_threshold, reclaim_raw, reclaim_factor,
        )
        if breakdown["final"] <= 0.0:
            diagnostic_strategy(snapshot.symbol, "S1", "rejected", "zero_confidence_factor")
            zero_factors = [
                key for key, value in breakdown.items()
                if isinstance(value, (int, float)) and value == 0.0
            ]
            logger.debug(
                "s1_candidate_rejected | symbol=%s | direction=SHORT | "
                "reason=zero_confidence | zero_factors=%s",
                snapshot.symbol, zero_factors,
            )
            return None
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": current.close_time_ms,
            "l_low": l_low,
            "sweep_dist_short": sweep_dist_short,
            "taker_buy_ratio": taker_buy_ratio,
            "penetration_min_atr": cfg["penetration_min_atr"],
            "reclaim_band_atr": reclaim_band_atr,
            "entry_offset_atr": entry_offset_atr,
            "sl_buffer_atr": sl_buffer_atr,
            "taker_sell_max_short": cfg["taker_sell_max_short"], "confidence_breakdown": breakdown,
        }

        diagnostic_strategy(snapshot.symbol, "S1", "candidate", "short_sweep_setup_valid", {"confidence": breakdown["final"]})
        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.SHORT,
            strategy_source="S1",
            confidence=breakdown["final"],
            channels=(ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            tp4=tp4,
            why_lines=why_lines,
            meta=meta,
            event_ts_ms=current.close_time_ms,
        )
