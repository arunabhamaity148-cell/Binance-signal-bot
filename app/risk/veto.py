"""The seven production veto guards G1, G2, G4, G5, G6, G8 and G9.

Every guard is a pure function `(MarketSnapshot, NewsState, CandidateSignal | None,
GuardConfig) -> GuardResult`. The production stack is intentionally venue-agnostic for Delta manual execution.
Each guard accepts the uniform `(MarketSnapshot, NewsState, CandidateSignal | None,
GuardConfig)` signature used by `veto_engine.py`.
"""

from __future__ import annotations

import math

from app.core.math import (
    InsufficientDataError,
    percentile_rank,
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
    NewsState,
)
from app.core.time_utils import is_stale
from app.data.binance.health import assess_feed_health


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
