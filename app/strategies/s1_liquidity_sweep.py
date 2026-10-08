"""S1 - Liquidity Sweep & Reclaim.

Full mathematical specification: STRATEGIES_SPEC.md section "S1 -
Liquidity Sweep & Reclaim". This module implements that specification
exactly, including every fail-closed condition.
"""

from __future__ import annotations

from app.core.math import InsufficientDataError, wilder_atr
from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    MarketSnapshot,
    NewsState,
)
from app.core.time_utils import is_stale
from app.strategies.base import StrategyBase


class S1LiquiditySweep(StrategyBase):
    strategy_id = "S1"

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

        bars_5m = snapshot.klines_for("5m")
        if len(bars_5m) < min_candles:
            return []  # fail-closed: insufficient history

        try:
            atr14 = wilder_atr(bars_5m, period=atr_period)
        except InsufficientDataError:
            return []  # fail-closed: ATR unavailable

        if atr14 <= 0:
            return []  # degenerate ATR, cannot compute sweep distances safely

        # Taker-flow freshness and availability check.
        if snapshot.taker_flow is None:
            return []  # fail-closed: taker flow required
        if is_stale(
            event_ts_ms=snapshot.taker_flow.event_ts_ms,
            received_ts_ms=snapshot.taker_flow.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms,
            staleness_budget_ms=cfg["taker_stale_ms"],
        ):
            return []  # fail-closed: taker flow stale

        try:
            taker_buy_ratio = snapshot.taker_flow.taker_buy_ratio
        except ValueError:
            return []  # fail-closed: no volume to compute ratio

        swing_lookback = cfg["swing_lookback"]
        if len(bars_5m) < swing_lookback + 2:
            return []

        i = len(bars_5m) - 1  # index of most recently closed bar
        window = bars_5m[i - swing_lookback : i]  # bars i-1 .. i-swing_lookback
        if not window:
            return []

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

        return self.finalize_candidates(candidates, snapshot, config)

    def _evaluate_long(
        self, *, snapshot, cfg, atr14, l_high, current, prev, bars_5m, taker_buy_ratio
    ) -> CandidateSignal | None:
        sweep_dist_long = (current.high - l_high) / atr14
        if sweep_dist_long < cfg["penetration_min_atr"]:
            return None
        if not (current.high > l_high):
            return None
        if not (current.close < l_high):
            return None
        reclaim_band_atr = cfg["reclaim_band_atr"]
        if not (current.close >= l_high - reclaim_band_atr * atr14):
            return None

        if taker_buy_ratio < cfg["taker_buy_min_long"]:
            return None

        entry_offset_atr = cfg["entry_offset_atr"]
        entry_low = l_high - entry_offset_atr * atr14
        entry_high = l_high + entry_offset_atr * atr14
        entry_mid = (entry_low + entry_high) / 2

        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = min(current.low, prev.low) - sl_buffer_atr * atr14

        if stop_loss >= entry_low:
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
            "taker_buy_min_long": cfg["taker_buy_min_long"],
        }

        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.LONG,
            strategy_source="S1",
            confidence=min(1.0, 0.5 + 0.1 * sweep_dist_long),
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
            return None
        if not (current.low < l_low):
            return None
        if not (current.close > l_low):
            return None
        reclaim_band_atr = cfg["reclaim_band_atr"]
        if not (current.close <= l_low + reclaim_band_atr * atr14):
            return None

        if taker_buy_ratio > cfg["taker_sell_max_short"]:
            return None

        entry_offset_atr = cfg["entry_offset_atr"]
        entry_low = l_low - entry_offset_atr * atr14
        entry_high = l_low + entry_offset_atr * atr14
        entry_mid = (entry_low + entry_high) / 2

        sl_buffer_atr = cfg["sl_buffer_atr"]
        stop_loss = max(current.high, prev.high) + sl_buffer_atr * atr14

        if stop_loss <= entry_high:
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
            "taker_sell_max_short": cfg["taker_sell_max_short"],
        }

        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.SHORT,
            strategy_source="S1",
            confidence=min(1.0, 0.5 + 0.1 * sweep_dist_short),
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
