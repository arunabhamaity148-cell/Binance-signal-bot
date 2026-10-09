"""Veto guards G1-G15.

Every guard is a pure function `(MarketSnapshot, NewsState, CandidateSignal | None,
GuardConfig) -> GuardResult`. Full specification: VETO_SPEC.md. Guards
that don't need the candidate (most feed/market-structure guards) still
accept it for a uniform call signature from veto_engine.py, and simply
ignore it.

G1, G2, G3, G4, G5, G10, G13, G14, G15 default to HARD BLOCK per spec.
G6, G8, G9, G11 DEGRADE. G7 blocks at HIGH/CRITICAL severity. G12 is a
HARD BLOCK self-consistency check.
"""

from __future__ import annotations

import math

from app.core.math import (
    InsufficientDataError,
    percentile_rank,
    rolling_median,
    wilder_atr,
    wilder_atr_series,
)
from app.core.models import (
    CandidateSignal,
    Direction,
    GuardAction,
    GuardResult,
    GuardSeverity,
    MarketSnapshot,
    NewsSeverity,
    NewsState,
)
from app.core.time_utils import is_stale
from app.data.binance.health import assess_feed_health
from app.data.derivatives import oi_series_for_window


def _pass(name: str, severity: GuardSeverity) -> GuardResult:
    return GuardResult(guard_name=name, passed=True, severity=severity, action=GuardAction.PASS, reason=None)


def _block(name: str, severity: GuardSeverity, reason: str) -> GuardResult:
    return GuardResult(guard_name=name, passed=False, severity=severity, action=GuardAction.BLOCK, reason=reason)


def _degrade(name: str, severity: GuardSeverity, reason: str, max_grade: str) -> GuardResult:
    return GuardResult(
        guard_name=name, passed=False, severity=severity, action=GuardAction.DEGRADE,
        reason=reason, degrade_max_grade=max_grade,
    )


# ---------------------------------------------------------------------------
# G1 - Data Integrity
# ---------------------------------------------------------------------------


def guard_g1_data_integrity(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    """Structural check: OHLC construction itself already enforces
    low<=open,close<=high (raises at construction, caught upstream), so
    by the time we have a MarketSnapshot, its klines are structurally
    valid. This guard re-verifies strict chronological ordering with no
    duplicates across every timeframe present, which is the remaining
    structural property not already enforced by OHLC's own validator.
    """
    for timeframe, sk in snapshot.klines.items():
        prev_ct = None
        for bar in sk.bars:
            if prev_ct is not None and bar.close_time_ms <= prev_ct:
                return _block(
                    "G1", GuardSeverity.CRITICAL,
                    f"out-of-order or duplicate kline in {timeframe}: {bar.close_time_ms} <= {prev_ct}",
                )
            prev_ct = bar.close_time_ms
    return _pass("G1", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G2 - Feed Health
# ---------------------------------------------------------------------------


def guard_g2_feed_health(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    if not snapshot.feed_health:
        # No feed health data supplied at all is itself unhealthy —
        # fail closed rather than assume healthy by omission.
        return _block("G2", GuardSeverity.CRITICAL, "no feed health data available in snapshot")

    for stream, health in snapshot.feed_health.items():
        assessment = assess_feed_health(
            health,
            as_of_ts_ms=snapshot.as_of_ts_ms,
            feed_gap_threshold_ms=cfg["feed_gap_threshold_ms"],
            max_reconnects_per_window=cfg["max_reconnects_per_window"],
        )
        if not assessment.is_healthy:
            return _block("G2", GuardSeverity.CRITICAL, f"{stream}: {assessment.reason}")
    return _pass("G2", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G3 - Liquidity / Depth Collapse
# ---------------------------------------------------------------------------


def guard_g3_depth_collapse(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict, *, symbol_tier: str) -> GuardResult:
    if snapshot.orderbook is None:
        return _block("G3", GuardSeverity.CRITICAL, "no orderbook data available")

    if is_stale(
        event_ts_ms=snapshot.orderbook.event_ts_ms,
        received_ts_ms=snapshot.orderbook.received_ts_ms,
        as_of_ts_ms=snapshot.as_of_ts_ms,
        staleness_budget_ms=cfg["depth_stale_ms"],
    ):
        return _block("G3", GuardSeverity.CRITICAL, "orderbook depth data is stale")

    min_depth = cfg["min_depth_usd"][symbol_tier]
    min_side_depth = min(snapshot.orderbook.bid_depth_5lvl_usd, snapshot.orderbook.ask_depth_5lvl_usd)
    if min_side_depth < min_depth:
        return _block(
            "G3", GuardSeverity.CRITICAL,
            f"depth {min_side_depth:.0f} USD below minimum {min_depth} USD for tier {symbol_tier}",
        )
    return _pass("G3", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G4 - Spread Explosion
# ---------------------------------------------------------------------------


def guard_g4_spread_explosion(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict, *, symbol_tier: str) -> GuardResult:
    if snapshot.orderbook is None:
        return _block("G4", GuardSeverity.HIGH, "no orderbook data available")

    if is_stale(
        event_ts_ms=snapshot.orderbook.event_ts_ms,
        received_ts_ms=snapshot.orderbook.received_ts_ms,
        as_of_ts_ms=snapshot.as_of_ts_ms,
        staleness_budget_ms=cfg["bookticker_stale_ms"],
    ):
        return _block("G4", GuardSeverity.HIGH, "bookTicker data is stale")

    if snapshot.orderbook.is_crossed:
        # Defers to G10 per VETO_SPEC.md edge case.
        return _pass("G4", GuardSeverity.HIGH)

    max_spread = cfg["max_spread_bps"][symbol_tier]
    spread_bps = snapshot.orderbook.spread_bps
    if spread_bps > max_spread:
        return _block(
            "G4", GuardSeverity.HIGH,
            f"spread {spread_bps:.2f} bps exceeds max {max_spread} bps for tier {symbol_tier}",
        )
    return _pass("G4", GuardSeverity.HIGH)


# ---------------------------------------------------------------------------
# G5 - OI Anomaly
# ---------------------------------------------------------------------------


def guard_g5_oi_anomaly(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    if snapshot.derivatives is None:
        return _block("G5", GuardSeverity.CRITICAL, "no derivatives data available")

    oi_series = snapshot.derivatives.open_interest_history_5m
    if len(oi_series) < 2:
        return _block("G5", GuardSeverity.CRITICAL, "insufficient OI history to assess anomaly")

    latest = oi_series[-1]
    if is_stale(
        event_ts_ms=latest.event_ts_ms,
        received_ts_ms=latest.received_ts_ms,
        as_of_ts_ms=snapshot.as_of_ts_ms,
        staleness_budget_ms=cfg["oi_stale_ms"],
    ):
        return _block("G5", GuardSeverity.CRITICAL, "OI data is stale")

    prev_value = oi_series[-2].value
    if prev_value == 0:
        return _block("G5", GuardSeverity.CRITICAL, "prior OI value is zero, cannot assess anomaly")

    pct_change = abs(latest.value - prev_value) / prev_value
    if pct_change > cfg["oi_anomaly_pct"]:
        return _block(
            "G5", GuardSeverity.CRITICAL,
            f"OI single-bar change {pct_change:.2%} exceeds anomaly threshold {cfg['oi_anomaly_pct']:.2%}",
        )
    return _pass("G5", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G6 - Funding Extreme
# ---------------------------------------------------------------------------


def guard_g6_funding_extreme(
    snapshot: MarketSnapshot, news: NewsState, candidate: CandidateSignal | None, cfg: dict, *, funding_z: float | None
) -> GuardResult:
    if funding_z is None:
        # Funding data unavailable is not itself a G6 block condition —
        # S3 already fails closed on missing funding at the strategy
        # level; G6 simply has nothing to degrade against.
        return _pass("G6", GuardSeverity.MEDIUM)

    if abs(funding_z) < cfg["hard_funding_z_threshold"]:
        return _pass("G6", GuardSeverity.MEDIUM)

    if candidate is not None and candidate.strategy_source == "S3":
        # Do not double-penalize S3, whose thesis already requires
        # this exact extreme as an input.
        return _pass("G6", GuardSeverity.MEDIUM)

    return _degrade(
        "G6", GuardSeverity.MEDIUM,
        f"funding_z {funding_z:.2f} exceeds hard threshold {cfg['hard_funding_z_threshold']}",
        cfg["degrade_max_grade"],
    )


# ---------------------------------------------------------------------------
# G7 - News Shock
# ---------------------------------------------------------------------------


def guard_g7_news_shock(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    severity = news.max_severity_for(snapshot.symbol)
    order = [NewsSeverity.LOW, NewsSeverity.MEDIUM, NewsSeverity.HIGH, NewsSeverity.CRITICAL]
    min_block_severity = NewsSeverity[cfg["min_severity_for_block"]]
    if order.index(severity) >= order.index(min_block_severity):
        return _block("G7", GuardSeverity.HIGH, f"active news severity {severity.value} for {snapshot.symbol}")
    return _pass("G7", GuardSeverity.HIGH)


# ---------------------------------------------------------------------------
# G8 - Volatility Flash
# ---------------------------------------------------------------------------


def guard_g8_volatility_flash(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    bars = snapshot.klines_for("5m")
    if len(bars) < 201:
        # Not enough history for the 200-bar percentile; conservatively
        # pass rather than block (this guard's job is catching flash
        # anomalies, not general data sufficiency — G1/snapshot
        # assembly already gate on minimum history for strategies).
        return _pass("G8", GuardSeverity.HIGH)

    try:
        atr14 = wilder_atr(bars, period=14)
        atr_series = wilder_atr_series(bars, period=14)
    except InsufficientDataError:
        return _pass("G8", GuardSeverity.HIGH)

    if atr14 <= 0:
        return _pass("G8", GuardSeverity.HIGH)

    current = bars[-1]
    bar_range = current.high - current.low
    if bar_range > cfg["bar_range_atr_mult"] * atr14:
        return _degrade(
            "G8", GuardSeverity.HIGH,
            f"bar range {bar_range:.6f} exceeds {cfg['bar_range_atr_mult']}x ATR",
            "A",
        )

    if len(atr_series) >= 201:
        history = atr_series[-201:-1]
        pct = percentile_rank(atr14, history)
        if pct > cfg["atr_percentile_extreme"]:
            return _degrade(
                "G8", GuardSeverity.HIGH,
                f"ATR percentile {pct:.2f} exceeds extreme threshold {cfg['atr_percentile_extreme']}",
                "A",
            )
    return _pass("G8", GuardSeverity.HIGH)


# ---------------------------------------------------------------------------
# G9 - BTC Regime
# ---------------------------------------------------------------------------


def guard_g9_btc_regime(
    snapshot: MarketSnapshot,
    news: NewsState,
    candidate: CandidateSignal | None,
    cfg: dict,
    *,
    btc_trend_direction: str | None,  # "up" | "down" | "flat" | None
) -> GuardResult:
    if candidate is None or snapshot.symbol == "BTCUSDT" or btc_trend_direction is None:
        return _pass("G9", GuardSeverity.MEDIUM)
    if candidate.strategy_source == "S3":
        return _pass("G9", GuardSeverity.MEDIUM)  # S3 reversal thesis exempted
    if btc_trend_direction == "flat":
        return _pass("G9", GuardSeverity.MEDIUM)

    counter_trend = (
        (btc_trend_direction == "up" and candidate.direction == Direction.SHORT)
        or (btc_trend_direction == "down" and candidate.direction == Direction.LONG)
    )
    if counter_trend:
        return _degrade(
            "G9", GuardSeverity.MEDIUM,
            f"{snapshot.symbol} candidate counter-trend to BTC regime ({btc_trend_direction})",
            cfg["degrade_max_grade"],
        )
    return _pass("G9", GuardSeverity.MEDIUM)


# ---------------------------------------------------------------------------
# G10 - Orderbook Instability
# ---------------------------------------------------------------------------


def guard_g10_orderbook_instability(snapshot: MarketSnapshot, news: NewsState, candidate, cfg: dict) -> GuardResult:
    if snapshot.orderbook is None:
        return _block("G10", GuardSeverity.CRITICAL, "no orderbook data available")

    ob = snapshot.orderbook
    if ob.is_crossed:
        return _block("G10", GuardSeverity.CRITICAL, f"crossed book: bid {ob.best_bid} >= ask {ob.best_ask}")
    if ob.is_empty:
        return _block("G10", GuardSeverity.CRITICAL, "empty orderbook side")

    ratio_threshold = cfg["lopsidedness_ratio"]
    bid_d, ask_d = ob.bid_depth_5lvl_usd, ob.ask_depth_5lvl_usd
    if ask_d > 0 and bid_d / ask_d > ratio_threshold:
        return _block("G10", GuardSeverity.CRITICAL, f"lopsided book: bid/ask depth ratio {bid_d/ask_d:.1f}")
    if bid_d > 0 and ask_d / bid_d > ratio_threshold:
        return _block("G10", GuardSeverity.CRITICAL, f"lopsided book: ask/bid depth ratio {ask_d/bid_d:.1f}")

    return _pass("G10", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G11 - Execution Quality
# ---------------------------------------------------------------------------


def guard_g11_execution_quality(
    snapshot: MarketSnapshot,
    news: NewsState,
    candidate: CandidateSignal | None,
    cfg: dict,
    *,
    symbol_tier: str,
    prior_results: list[GuardResult],
) -> GuardResult:
    # Short-circuit if G3 or G4 already hard-blocked.
    for r in prior_results:
        if r.guard_name in ("G3", "G4") and not r.passed and r.action == GuardAction.BLOCK:
            return _pass("G11", GuardSeverity.MEDIUM)

    if candidate is None or snapshot.orderbook is None:
        return _pass("G11", GuardSeverity.MEDIUM)

    from app.signals.tpsl import estimate_slippage_bps

    entry_price = (candidate.entry_low + candidate.entry_high) / 2
    notional = entry_price * 1.0  # nominal 1-unit notional for guard-level estimation
    depth_usd = min(snapshot.orderbook.bid_depth_5lvl_usd, snapshot.orderbook.ask_depth_5lvl_usd)
    slippage_bps = estimate_slippage_bps(notional, depth_usd)

    max_acceptable = cfg["max_acceptable_slippage_bps"][symbol_tier]
    if slippage_bps > max_acceptable * cfg["escalation_multiple"]:
        return _block("G11", GuardSeverity.MEDIUM, f"slippage estimate {slippage_bps:.1f} bps far exceeds threshold")
    if slippage_bps > max_acceptable:
        return _degrade(
            "G11", GuardSeverity.MEDIUM,
            f"slippage estimate {slippage_bps:.1f} bps exceeds {max_acceptable} bps for tier {symbol_tier}",
            "A",
        )
    return _pass("G11", GuardSeverity.MEDIUM)


# ---------------------------------------------------------------------------
# G12 - Strategy / Data Integrity (self-consistency)
# ---------------------------------------------------------------------------


def guard_g12_self_consistency(
    snapshot: MarketSnapshot, news: NewsState, candidate: CandidateSignal | None, cfg: dict
) -> GuardResult:
    if candidate is None:
        return _pass("G12", GuardSeverity.CRITICAL)

    required_keys = ("atr14", "snapshot_version", "event_ts_ms")
    missing = [k for k in required_keys if k not in candidate.meta]
    if missing:
        return _block("G12", GuardSeverity.CRITICAL, f"candidate meta missing required keys: {missing}")

    if candidate.meta["snapshot_version"] != snapshot.snapshot_version:
        return _block(
            "G12", GuardSeverity.CRITICAL,
            f"candidate meta snapshot_version {candidate.meta['snapshot_version']} "
            f"does not match current snapshot {snapshot.snapshot_version}",
        )

    recorded_atr = candidate.meta["atr14"]
    try:
        bars = snapshot.klines_for("5m")
        recomputed_atr = wilder_atr(bars, period=14)
    except (InsufficientDataError, Exception):  # noqa: BLE001 - any recompute failure is itself a mismatch
        return _block("G12", GuardSeverity.CRITICAL, "failed to recompute ATR14 for self-consistency check")

    rel_tol = cfg["relative_tolerance"]
    if recorded_atr == 0:
        matches = recomputed_atr == 0
    else:
        matches = abs(recomputed_atr - recorded_atr) / abs(recorded_atr) <= rel_tol
    if not matches:
        return _block(
            "G12", GuardSeverity.CRITICAL,
            f"recomputed ATR14 {recomputed_atr} does not match recorded {recorded_atr} within tolerance",
        )

    return _pass("G12", GuardSeverity.CRITICAL)


# ---------------------------------------------------------------------------
# G13 - OI Divergence
# ---------------------------------------------------------------------------


def guard_g13_oi_divergence(
    snapshot: MarketSnapshot, news: NewsState, candidate: CandidateSignal | None, cfg: dict
) -> GuardResult:
    if candidate is None:
        return _pass("G13", GuardSeverity.HIGH)
    if candidate.strategy_source in cfg["exempt_strategies"]:
        return _pass("G13", GuardSeverity.HIGH)
    if snapshot.derivatives is None:
        return _pass("G13", GuardSeverity.HIGH)  # nothing to check divergence against

    bars = snapshot.klines_for("5m")
    window = cfg["divergence_window"]
    oi_series = snapshot.derivatives.open_interest_history_5m
    if len(bars) <= window or len(oi_series) <= window:
        return _pass("G13", GuardSeverity.HIGH)

    price_now = bars[-1].close
    price_prior = bars[-(window + 1)].close
    oi_now = oi_series[-1].value
    oi_prior = oi_series[-(window + 1)].value
    if oi_prior == 0:
        return _pass("G13", GuardSeverity.HIGH)

    price_sign = 1 if price_now > price_prior else (-1 if price_now < price_prior else 0)
    oi_pct = (oi_now - oi_prior) / oi_prior
    oi_sign = 1 if oi_pct > 0 else (-1 if oi_pct < 0 else 0)

    if price_sign != 0 and oi_sign != 0 and price_sign != oi_sign and abs(oi_pct) >= cfg["oi_divergence_min_pct"]:
        return _block(
            "G13", GuardSeverity.HIGH,
            f"price/OI divergence: price_sign={price_sign} oi_sign={oi_sign} oi_pct={oi_pct:.2%}",
        )
    return _pass("G13", GuardSeverity.HIGH)


# ---------------------------------------------------------------------------
# G14 - OI Stagnation
# ---------------------------------------------------------------------------


def guard_g14_oi_stagnation(
    snapshot: MarketSnapshot, news: NewsState, candidate: CandidateSignal | None, cfg: dict
) -> GuardResult:
    if candidate is None:
        return _pass("G14", GuardSeverity.MEDIUM)
    if candidate.strategy_source in cfg["exempt_strategies"]:
        return _pass("G14", GuardSeverity.MEDIUM)
    if snapshot.derivatives is None:
        return _pass("G14", GuardSeverity.MEDIUM)

    bars = snapshot.klines_for("5m")
    window = int(cfg.get("window_bars", 12))
    oi_series = snapshot.derivatives.open_interest_history_5m
    if len(bars) <= window or len(oi_series) <= window:
        return _pass("G14", GuardSeverity.MEDIUM)

    try:
        atr14 = wilder_atr(bars, period=14)
    except InsufficientDataError:
        return _pass("G14", GuardSeverity.MEDIUM)
    if atr14 <= 0:
        return _pass("G14", GuardSeverity.MEDIUM)

    price_now = bars[-1].close
    price_prior = bars[-(window + 1)].close
    oi_now = oi_series[-1].value
    oi_prior = oi_series[-(window + 1)].value
    if oi_prior == 0:
        return _pass("G14", GuardSeverity.MEDIUM)

    oi_pct = abs((oi_now - oi_prior) / oi_prior)
    price_move_atr = abs(price_now - price_prior) / atr14

    if oi_pct < cfg["oi_noise_band"] and price_move_atr > cfg["strong_move_atr_mult"]:
        return _degrade(
            "G14", GuardSeverity.MEDIUM,
            f"G14 OI stagnation: flat OI ({oi_pct:.2%}) with strong price move ({price_move_atr:.2f} ATR)",
            cfg["degrade_max_grade"],
        )
    return _pass("G14", GuardSeverity.MEDIUM)


# ---------------------------------------------------------------------------
# G15 - OI Percentile Extreme
# ---------------------------------------------------------------------------


def guard_g15_oi_percentile_extreme(
    snapshot: MarketSnapshot, news: NewsState, candidate: CandidateSignal | None, cfg: dict
) -> GuardResult:
    if candidate is None or candidate.strategy_source not in cfg["applies_to_strategies"]:
        return _pass("G15", GuardSeverity.HIGH)
    if snapshot.derivatives is None:
        return _block("G15", GuardSeverity.HIGH, "no derivatives data to assess OI percentile")

    oi_series = snapshot.derivatives.open_interest_history_1d
    min_days = cfg["min_history_days"]
    if len(oi_series) < min_days:
        return _block(
            "G15", GuardSeverity.HIGH,
            f"insufficient 30-day OI history ({len(oi_series)} < {min_days} days) for {candidate.strategy_source}",
        )

    current_oi = snapshot.derivatives.open_interest_history_5m[-1].value if snapshot.derivatives.open_interest_history_5m else None
    if current_oi is None:
        return _block("G15", GuardSeverity.HIGH, "no current OI value available")

    history_values = [tv.value for tv in oi_series]
    pct = percentile_rank(current_oi, history_values)

    if pct > cfg["high_threshold"] or pct < cfg["low_threshold"]:
        return _block("G15", GuardSeverity.HIGH, f"G15 OI percentile extreme: {pct:.2f} for trend-continuation strategy")
    return _pass("G15", GuardSeverity.HIGH)
