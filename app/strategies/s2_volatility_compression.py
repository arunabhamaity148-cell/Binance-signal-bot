"""S2 - Volatility Compression -> Range Expansion.

Full mathematical specification: STRATEGIES_SPEC.md section "S2 -
Volatility Compression -> Range Expansion". This module implements
that specification exactly, including every fail-closed condition.

R CONVENTION: entry is a single price (the retest level or the
breakout bar's close, per `entry_mode`), so entry_low == entry_high ==
entry and the entry-zone-width filter in StrategyBase.finalize_candidates
is trivially satisfied (width 0). R and every TP are computed from that
single entry price, which is also its own midpoint.
"""

from __future__ import annotations

from app.core.math import (
    InsufficientDataError,
    rolling_median,
    wilder_atr,
    wilder_atr_series,
    percentile_rank,
)
from app.core.models import (
    CandidateSignal,
    ChannelName,
    Direction,
    MarketSnapshot,
    NewsState,
)
from app.core.time_utils import is_stale
from app.data.derivatives import oi_series_for_window
from app.strategies.base import StrategyBase


class S2VolatilityCompression(StrategyBase):
    strategy_id = "S2"

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        cfg = config["s2_volatility_compression"]
        common = config["common"]
        atr_period = common["atr_period"]
        min_candles = common["min_candles"]

        range_bars = cfg["range_bars"]
        percentile_window = cfg["atr_percentile_window"]

        bars_5m = snapshot.klines_for("5m")
        # Fail-closed: fewer than N + 200 bars of 5m history (for the
        # percentile calc) -> NO TRADE. Also never below the shared
        # min_candles floor.
        required = max(min_candles, range_bars + percentile_window + atr_period + 1)
        if len(bars_5m) < required:
            return []

        bars_15m = snapshot.klines_for("15m")
        if len(bars_15m) < 2:
            return []  # 15m compression confirmation context required

        try:
            atr_series = wilder_atr_series(bars_5m, period=atr_period)
        except InsufficientDataError:
            return []
        if len(atr_series) < percentile_window + 1:
            return []

        atr14 = atr_series[-1]
        if atr14 <= 0:
            return []

        atr_history = atr_series[-(percentile_window + 1):-1]
        atr_percentile = percentile_rank(atr14, atr_history)

        i = len(bars_5m) - 1
        window_bars = bars_5m[i - range_bars:i]  # bars i-N .. i-1, excludes current (no look-ahead)
        if len(window_bars) < range_bars:
            return []

        range_high = max(b.high for b in window_bars)
        range_low = min(b.low for b in window_bars)
        range_width_atr = (range_high - range_low) / atr14

        compression = (
            range_width_atr <= cfg["range_width_max_atr_mult"]
            and atr_percentile <= cfg["atr_percentile_max"]
        )
        if not compression:
            return []

        volumes = [b.volume for b in window_bars]
        try:
            median_volume = rolling_median(volumes)
        except InsufficientDataError:
            return []
        if median_volume <= 0:
            return []

        # OI confirmation is mandatory (S2 does not degrade to price-only).
        if snapshot.derivatives is None:
            return []
        oi_series_5m = snapshot.derivatives.open_interest_history_5m
        if len(oi_series_5m) < 2:
            return []
        latest_oi = oi_series_5m[-1]
        if is_stale(
            event_ts_ms=latest_oi.event_ts_ms,
            received_ts_ms=latest_oi.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms,
            staleness_budget_ms=cfg["oi_stale_ms"],
        ):
            return []
        oi_now = oi_series_5m[-1].value
        oi_prior = oi_series_5m[-2].value
        if oi_prior == 0:
            return []
        oi_delta_pct = (oi_now - oi_prior) / oi_prior * 100

        current = bars_5m[i]
        body = current.close - current.open
        volume_confirm_ratio = current.volume / median_volume

        candidates: list[CandidateSignal] = []

        long_candidate = self._evaluate_long(
            snapshot=snapshot, cfg=cfg, atr14=atr14, range_high=range_high,
            current=current, body=body, volume_confirm_ratio=volume_confirm_ratio,
            oi_delta_pct=oi_delta_pct, range_width_atr=range_width_atr, atr_percentile=atr_percentile,
        )
        if long_candidate is not None:
            candidates.append(long_candidate)

        short_candidate = self._evaluate_short(
            snapshot=snapshot, cfg=cfg, atr14=atr14, range_low=range_low,
            current=current, body=body, volume_confirm_ratio=volume_confirm_ratio,
            oi_delta_pct=oi_delta_pct, range_width_atr=range_width_atr, atr_percentile=atr_percentile,
        )
        if short_candidate is not None:
            candidates.append(short_candidate)

        return self.finalize_candidates(candidates, snapshot, config)

    def _evaluate_long(
        self, *, snapshot, cfg, atr14, range_high, current, body, volume_confirm_ratio,
        oi_delta_pct, range_width_atr, atr_percentile,
    ) -> CandidateSignal | None:
        if not (body > cfg["expansion_atr_mult"] * atr14):
            return None
        if not (current.close > range_high):
            return None
        if not (volume_confirm_ratio > cfg["volume_confirm_ratio"]):
            return None
        if not (oi_delta_pct >= cfg["min_oi_chg_pct"]):
            return None

        entry_mode = cfg["entry_mode"]
        if entry_mode == "retest":
            entry = range_high
        elif entry_mode == "market":
            entry = current.close
        else:
            raise ValueError(f"unknown s2 entry_mode: {entry_mode!r}")

        sl_boundary_buffer_atr = cfg["sl_boundary_buffer_atr"]
        stop_loss = range_high - sl_boundary_buffer_atr * atr14
        if stop_loss >= entry:
            return None

        r = entry - stop_loss
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry + m * r for m in tp_multiples)

        why_lines = (
            f"S2 LONG expansion: range_width {range_width_atr:.2f} ATR, "
            f"ATR percentile {atr_percentile:.2f} (compression confirmed)",
            f"Breakout body {body:.6f} > {cfg['expansion_atr_mult']} ATR, "
            f"close {current.close:.6f} > range_high {range_high:.6f}",
            f"Volume confirm ratio {volume_confirm_ratio:.2f} > {cfg['volume_confirm_ratio']}, "
            f"OI delta {oi_delta_pct:.2f}% >= {cfg['min_oi_chg_pct']}%",
        )
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": current.close_time_ms,
            "range_high": range_high,
            "range_width_atr": range_width_atr,
            "atr_percentile": atr_percentile,
            "volume_confirm_ratio": volume_confirm_ratio,
            "oi_delta_pct": oi_delta_pct,
            "entry_mode": entry_mode,
            "range_width_max_atr_mult": cfg["range_width_max_atr_mult"],
            "atr_percentile_max": cfg["atr_percentile_max"],
            "expansion_atr_mult": cfg["expansion_atr_mult"],
            "sl_boundary_buffer_atr": sl_boundary_buffer_atr,
            "min_oi_chg_pct": cfg["min_oi_chg_pct"],
        }

        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.LONG,
            strategy_source="S2",
            confidence=min(1.0, 0.5 + 0.1 * volume_confirm_ratio),
            channels=(ChannelName.VOLATILITY, ChannelName.OI),
            entry_low=entry,
            entry_high=entry,
            stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            why_lines=why_lines,
            meta=meta,
            event_ts_ms=current.close_time_ms,
        )

    def _evaluate_short(
        self, *, snapshot, cfg, atr14, range_low, current, body, volume_confirm_ratio,
        oi_delta_pct, range_width_atr, atr_percentile,
    ) -> CandidateSignal | None:
        if not (body < -cfg["expansion_atr_mult"] * atr14):
            return None
        if not (current.close < range_low):
            return None
        if not (volume_confirm_ratio > cfg["volume_confirm_ratio"]):
            return None
        if not (oi_delta_pct >= cfg["min_oi_chg_pct"]):
            return None

        entry_mode = cfg["entry_mode"]
        if entry_mode == "retest":
            entry = range_low
        elif entry_mode == "market":
            entry = current.close
        else:
            raise ValueError(f"unknown s2 entry_mode: {entry_mode!r}")

        sl_boundary_buffer_atr = cfg["sl_boundary_buffer_atr"]
        stop_loss = range_low + sl_boundary_buffer_atr * atr14
        if stop_loss <= entry:
            return None

        r = stop_loss - entry
        tp_multiples = cfg["tp_r_multiples"]
        tp1, tp2, tp3, tp4 = (entry - m * r for m in tp_multiples)

        why_lines = (
            f"S2 SHORT expansion: range_width {range_width_atr:.2f} ATR, "
            f"ATR percentile {atr_percentile:.2f} (compression confirmed)",
            f"Breakdown body {body:.6f} < -{cfg['expansion_atr_mult']} ATR, "
            f"close {current.close:.6f} < range_low {range_low:.6f}",
            f"Volume confirm ratio {volume_confirm_ratio:.2f} > {cfg['volume_confirm_ratio']}, "
            f"OI delta {oi_delta_pct:.2f}% >= {cfg['min_oi_chg_pct']}%",
        )
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": current.close_time_ms,
            "range_low": range_low,
            "range_width_atr": range_width_atr,
            "atr_percentile": atr_percentile,
            "volume_confirm_ratio": volume_confirm_ratio,
            "oi_delta_pct": oi_delta_pct,
            "entry_mode": entry_mode,
            "range_width_max_atr_mult": cfg["range_width_max_atr_mult"],
            "atr_percentile_max": cfg["atr_percentile_max"],
            "expansion_atr_mult": cfg["expansion_atr_mult"],
            "sl_boundary_buffer_atr": sl_boundary_buffer_atr,
            "min_oi_chg_pct": cfg["min_oi_chg_pct"],
        }

        return CandidateSignal(
            symbol=snapshot.symbol,
            direction=Direction.SHORT,
            strategy_source="S2",
            confidence=min(1.0, 0.5 + 0.1 * volume_confirm_ratio),
            channels=(ChannelName.VOLATILITY, ChannelName.OI),
            entry_low=entry,
            entry_high=entry,
            stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            why_lines=why_lines,
            meta=meta,
            event_ts_ms=current.close_time_ms,
        )
