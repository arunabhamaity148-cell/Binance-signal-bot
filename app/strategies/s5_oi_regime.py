"""S5 - OI Regime Shift.

Full mathematical specification: STRATEGIES_SPEC.md section "S5 - OI
Regime Shift". This module implements that specification exactly.

DOUBLE-COUNTING: OI percentile + OI delta (all windows) collapse into
a single OI channel vote. Taker flow is its own TAKER_FLOW channel.
Funding acts purely as a VETO FILTER here -- it can only remove
confidence (veto the candidate), never add it, so it is deliberately
excluded from `channels` (see app/risk/channels.py: S5's channel tuple
is (OI, TAKER_FLOW), no FUNDING entry) -- this keeps it out of the
effective-vote weight sum entirely, per spec section 11.

R CONVENTION: entry is a single retest/market price, so
entry_low == entry_high == entry, trivially satisfying the entry-zone-
width filter.

MIN R:R GATE AT STRATEGY LEVEL: per spec, S5 additionally requires
post-cost R:R at TP2 > min_rr_tp2 BEFORE the candidate is even passed
to consensus (not deferred purely to the risk engine). See
config/strategy.yaml's s5_oi_regime.min_rr_tp2 comment for why this
value is duplicated from risk.yaml rather than imported at runtime.
"""

from __future__ import annotations

from app.backtest.costs import compute_cost_breakdown, compute_rr_at_tp
from app.monitoring.diagnostics import strategy as diagnostic_strategy
from app.core.math import InsufficientDataError, percentile_rank, wilder_atr, zscore
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


class S5OiRegime(StrategyBase):
    strategy_id = "S5"

    @staticmethod
    def _skip(snapshot, reason: str):
        diagnostic_strategy(snapshot.symbol, "S5", "rejected", reason)
        logger.debug(
            "s5_eval_skip",
            extra={"context": {"symbol": snapshot.symbol, "reason": reason}},
        )
        return []

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        news_state: NewsState,
        config: dict,
    ) -> list[CandidateSignal]:
        cfg = config["s5_oi_regime"]
        common = config["common"]
        atr_period = common["atr_period"]
        min_candles = common["min_candles"]

        bars_5m = snapshot.klines_for("5m")
        if len(bars_5m) < min_candles:
            return self._skip(snapshot, "if len(bars_5m) < min_candles:")

        if snapshot.derivatives is None:
            return self._skip(snapshot, "if snapshot.derivatives is None:")

        deriv = snapshot.derivatives
        oi_stale_ms = cfg["oi_stale_ms"]
        oi_series = deriv.open_interest_history_5m
        if len(oi_series) < 13:  # need i, i-1, i-3, i-12
            return self._skip(snapshot, "if len(oi_series) < 13:  # need i, i-1, i-3, i-12")
        latest_oi = oi_series[-1]
        if is_stale(
            event_ts_ms=latest_oi.event_ts_ms, received_ts_ms=latest_oi.received_ts_ms,
            as_of_ts_ms=snapshot.as_of_ts_ms, staleness_budget_ms=oi_stale_ms,
        ):
            return self._skip(snapshot, "):")

        oi_history_1d = deriv.open_interest_history_1d
        window_days = cfg["oi_percentile_window_days"]
        if len(oi_history_1d) < window_days:
            return self._skip(snapshot, "if len(oi_history_1d) < window_days:")

        try:
            atr14 = wilder_atr(bars_5m, period=atr_period)
        except InsufficientDataError:
            return self._skip(snapshot, "except InsufficientDataError:")
        if atr14 <= 0:
            return self._skip(snapshot, "if atr14 <= 0:")

        oi_now = oi_series[-1].value
        oi_1_ago = oi_series[-2].value
        oi_3_ago = oi_series[-4].value
        oi_12_ago = oi_series[-13].value
        if 0 in (oi_1_ago, oi_3_ago, oi_12_ago):
            return self._skip(snapshot, "if 0 in (oi_1_ago, oi_3_ago, oi_12_ago):")

        oi_delta_5m = (oi_now - oi_1_ago) / oi_1_ago
        oi_delta_15m = (oi_now - oi_3_ago) / oi_3_ago
        oi_delta_1h = (oi_now - oi_12_ago) / oi_12_ago

        noise_band = cfg["oi_noise_band"]
        if abs(oi_delta_5m) < noise_band:
            return self._skip(snapshot, "if abs(oi_delta_5m) < noise_band:")

        oi_history_values = [tv.value for tv in oi_history_1d]
        oi_pct_now = percentile_rank(oi_now, oi_history_values)

        regime_shift_window = cfg["regime_shift_window"]
        if len(oi_series) < regime_shift_window + 1:
            return self._skip(snapshot, "if len(oi_series) < regime_shift_window + 1:")
        earlier_point = oi_series[-(regime_shift_window + 1)].value
        # Percentile of the earlier point against the SAME 1d history
        # window (a reasonable, consistent baseline for "where was OI
        # percentile-wise before this window" -- the 1d history itself
        # doesn't have per-5m-bar resolution to look back exactly
        # regime_shift_window bars within it).
        oi_pct_earlier = percentile_rank(earlier_point, oi_history_values)

        regime_low = cfg["regime_low_threshold"]
        regime_high = cfg["regime_high_threshold"]
        shift_up = oi_pct_earlier < regime_low and oi_pct_now > regime_high
        shift_down = oi_pct_earlier > regime_high and oi_pct_now < regime_low
        if not (shift_up or shift_down):
            return self._skip(snapshot, "if not (shift_up or shift_down):")

        price_now = bars_5m[-1].close
        price_1_ago = bars_5m[-2].close
        price_up = price_now > price_1_ago
        price_down = price_now < price_1_ago
        oi_up = oi_delta_5m > 0
        oi_down = oi_delta_5m < 0

        if price_up and oi_up:
            quadrant = "price_up_oi_up"
        elif price_up and oi_down:
            quadrant = "price_up_oi_down"
        elif price_down and oi_up:
            quadrant = "price_down_oi_up"
        elif price_down and oi_down:
            quadrant = "price_down_oi_down"
        else:
            return self._skip(snapshot, "else:")

        # Weak quadrants are explicitly not standalone signals.
        if quadrant in ("price_up_oi_down", "price_down_oi_down"):
            return self._skip(snapshot, "if quadrant in ('price_up_oi_down', 'price_down_oi_down'):")

        if snapshot.taker_flow is None:
            return self._skip(snapshot, "if snapshot.taker_flow is None:")
        try:
            taker_buy_ratio = snapshot.taker_flow.taker_buy_ratio
        except ValueError:
            return self._skip(snapshot, "except ValueError:")

        # Taker flow alignment with quadrant direction.
        if quadrant == "price_up_oi_up":
            direction = Direction.LONG
            taker_aligned = taker_buy_ratio > 0.5
        elif quadrant == "price_down_oi_up":
            direction = Direction.SHORT
            taker_aligned = taker_buy_ratio < 0.5
        else:
            return self._skip(snapshot, "else:")
        if not taker_aligned:
            return self._skip(snapshot, "if not taker_aligned:")

        # Funding veto-only filter: NOT a positive vote (see module
        # docstring). Reuses S3's funding_z calculation convention.
        funding_veto_z = cfg["funding_veto_z"]
        funding_z_value = None
        if deriv.funding_rate_history and len(deriv.funding_rate_history) >= 2:
            funding_values = [tv.value for tv in deriv.funding_rate_history]
            try:
                funding_z_value = zscore(funding_values[-1], funding_values)
            except InsufficientDataError:
                funding_z_value = None
        if funding_z_value is not None:
            funding_opposes_long = direction == Direction.LONG and funding_z_value < -funding_veto_z
            funding_opposes_short = direction == Direction.SHORT and funding_z_value > funding_veto_z
            if funding_opposes_long or funding_opposes_short:
                return self._skip(snapshot, "if funding_opposes_long or funding_opposes_short:")

        entry_mode = cfg["entry_mode"]
        entry = bars_5m[-1].close if entry_mode == "market" else bars_5m[-1].close
        # "retest" and "market" are both price-of-last-close here since
        # S5 has no explicit structure level like S1/S3/S2's range
        # boundary; the distinction is preserved in config/meta for
        # future refinement but both modes currently resolve to the
        # same value, which the meta.entry_mode records honestly.

        sl_buffer_atr = cfg["sl_buffer_atr"]
        if direction == Direction.LONG:
            stop_loss = entry - sl_buffer_atr * atr14
            if stop_loss >= entry:
                return None
        else:
            stop_loss = entry + sl_buffer_atr * atr14
            if stop_loss <= entry:
                return None

        candidate = self._build_candidate(
            snapshot=snapshot, cfg=cfg, direction=direction, entry=entry, stop_loss=stop_loss,
            atr14=atr14, quadrant=quadrant, oi_pct_now=oi_pct_now, oi_pct_earlier=oi_pct_earlier,
            oi_delta_5m=oi_delta_5m, oi_delta_15m=oi_delta_15m, oi_delta_1h=oi_delta_1h,
            taker_buy_ratio=taker_buy_ratio, funding_z_value=funding_z_value, entry_mode=entry_mode,
            bars_5m=bars_5m,
        )
        if candidate is None:
            return self._skip(snapshot, "if candidate is None:")

        # Strategy-level min R:R gate (before consensus), per spec.
        min_rr_tp2 = cfg["min_rr_tp2"]
        cost = compute_cost_breakdown(
            entry_price=entry, stop_price=stop_loss,
            fee_maker_bps=snapshot.fee_maker_bps, fee_taker_bps=snapshot.fee_taker_bps,
            notional_usd=entry, depth_usd=(
                min(snapshot.orderbook.bid_depth_5lvl_usd, snapshot.orderbook.ask_depth_5lvl_usd)
                if snapshot.orderbook is not None else None
            ),
        )
        try:
            rr_tp2 = compute_rr_at_tp(
                direction=direction, entry_price=entry, stop_loss=stop_loss,
                take_profit=candidate.tp2, cost=cost,
            )
        except ValueError:
            return self._skip(snapshot, "except ValueError:")
        if rr_tp2 <= min_rr_tp2:
            return self._skip(snapshot, "if rr_tp2 <= min_rr_tp2:")

        return self.finalize_candidates([candidate], snapshot, config)

    def _build_candidate(
        self, *, snapshot, cfg, direction, entry, stop_loss, atr14, quadrant, oi_pct_now,
        oi_pct_earlier, oi_delta_5m, oi_delta_15m, oi_delta_1h, taker_buy_ratio,
        funding_z_value, entry_mode, bars_5m,
    ) -> CandidateSignal | None:
        if direction == Direction.LONG:
            r = entry - stop_loss
        else:
            r = stop_loss - entry
        if r <= 0:
            return None
        tp_multiples = cfg["tp_r_multiples"]
        sign = 1 if direction == Direction.LONG else -1
        tp1, tp2, tp3, tp4 = (entry + sign * m * r for m in tp_multiples)

        why_lines = (
            f"S5 {direction.value} regime shift: quadrant {quadrant}, "
            f"oi_pct {oi_pct_earlier:.2f} -> {oi_pct_now:.2f}",
            f"OI delta 5m/15m/1h = {oi_delta_5m:.3%}/{oi_delta_15m:.3%}/{oi_delta_1h:.3%}",
            f"Taker flow aligned (buy ratio {taker_buy_ratio:.2f})"
            + (f"; funding_z {funding_z_value:.2f} did not veto" if funding_z_value is not None else ""),
        )
        meta = {
            "atr14": atr14,
            "snapshot_version": snapshot.snapshot_version,
            "event_ts_ms": bars_5m[-1].close_time_ms,
            "quadrant": quadrant,
            "oi_pct_now": oi_pct_now,
            "oi_pct_earlier": oi_pct_earlier,
            "oi_delta_5m": oi_delta_5m,
            "oi_delta_15m": oi_delta_15m,
            "oi_delta_1h": oi_delta_1h,
            "taker_buy_ratio": taker_buy_ratio,
            "funding_z": funding_z_value,
            "entry_mode": entry_mode,
            "sl_buffer_atr": cfg["sl_buffer_atr"],
            "oi_noise_band": cfg["oi_noise_band"],
            "regime_low_threshold": cfg["regime_low_threshold"],
            "regime_high_threshold": cfg["regime_high_threshold"],
        }

        confidence = min(1.0, 0.5 + abs(oi_delta_5m) * 5)
        logger.info("s5_confidence_breakdown", extra={"context": {"symbol": snapshot.symbol,
            "oi_delta_5m": oi_delta_5m, "taker_buy_ratio": taker_buy_ratio,
            "funding_veto_z": funding_z_value, "final": confidence}})
        meta["confidence_breakdown"] = {"oi_delta_5m": oi_delta_5m,
                                         "taker_buy_ratio": taker_buy_ratio,
                                         "funding_veto_z": funding_z_value, "final": confidence}
        logger.debug("s5_candidate_created | symbol=%s | direction=%s | confidence=%s", snapshot.symbol, direction.value, confidence)
        return CandidateSignal(
            symbol=snapshot.symbol, direction=direction, strategy_source="S5",
            confidence=confidence,
            channels=(ChannelName.OI, ChannelName.TAKER_FLOW),
            entry_low=entry, entry_high=entry, stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            why_lines=why_lines, meta=meta, event_ts_ms=bars_5m[-1].close_time_ms,
        )
