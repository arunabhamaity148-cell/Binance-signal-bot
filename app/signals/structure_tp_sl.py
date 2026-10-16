"""Feature-flagged structure-aware TP/SL policy.

This module is deliberately separate from the legacy strategy math.  It is
used in shadow mode first: the caller can record the proposed levels without
changing the Telegram payload.  No policy here places, modifies, or cancels
orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from app.core.models import CandidateSignal, Direction, MarketSnapshot


@dataclass(frozen=True)
class StructurePolicyConfig:
    buffer_atr: float = 0.20
    min_rr_tp2: float = 1.8
    liquidity_lookback_bars: int = 60
    max_target_distance_r: float = 5.0


@dataclass(frozen=True)
class StructurePolicyResult:
    accepted: bool
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    reason: str
    source: str


def _historical_liquidity(snapshot: MarketSnapshot, direction: Direction, entry: float, lookback: int) -> list[float]:
    """Return opposing liquidity from bars already closed at snapshot time."""
    levels: list[float] = []
    for timeframe in ("5m", "15m", "1h", "4h"):
        bars = snapshot.klines_for(timeframe)[-lookback:]
        if direction == Direction.LONG:
            levels.extend(bar.high for bar in bars if bar.high > entry)
        else:
            levels.extend(bar.low for bar in bars if bar.low < entry)
    return sorted(set(levels), reverse=direction == Direction.SHORT)


def _structure_stop(candidate: CandidateSignal, entry: float, atr: float, cfg: StructurePolicyConfig) -> tuple[float, str]:
    meta = candidate.meta
    buffer = max(0.0, cfg.buffer_atr) * atr
    direction = candidate.direction
    # Explicit structure levels have priority.  The fallback to the legacy
    # stop is intentional for S4/S5 until their structure metadata is richer.
    if candidate.strategy_source == "S1":
        extreme = meta.get("sweep_extreme")
        if isinstance(extreme, (int, float)):
            return ((float(extreme) - buffer, "S1_sweep_extreme") if direction == Direction.LONG
                    else (float(extreme) + buffer, "S1_sweep_extreme"))
    if candidate.strategy_source == "S2":
        boundary = meta.get("range_low" if direction == Direction.LONG else "range_high")
        if isinstance(boundary, (int, float)):
            return ((float(boundary) - buffer, "S2_compression_boundary") if direction == Direction.LONG
                    else (float(boundary) + buffer, "S2_compression_boundary"))
    if candidate.strategy_source == "S3":
        level = meta.get("structure_level")
        if isinstance(level, (int, float)):
            return ((float(level) - buffer, "S3_squeeze_origin") if direction == Direction.LONG
                    else (float(level) + buffer, "S3_squeeze_origin"))
    if candidate.strategy_source == "S4":
        level = meta.get("pullback_structure") or meta.get("ema_fast")
        if isinstance(level, (int, float)):
            return ((float(level) - buffer, "S4_pullback_structure") if direction == Direction.LONG
                    else (float(level) + buffer, "S4_pullback_structure"))
    if candidate.strategy_source == "S5":
        level = meta.get("regime_structure")
        if isinstance(level, (int, float)):
            return ((float(level) - buffer, "S5_regime_structure") if direction == Direction.LONG
                    else (float(level) + buffer, "S5_regime_structure"))
    return candidate.stop_loss, "legacy_stop_fallback"


def apply_structure_aware_policy(
    *, candidate: CandidateSignal, snapshot: MarketSnapshot,
    config: StructurePolicyConfig = StructurePolicyConfig(),
) -> StructurePolicyResult:
    entry_low, entry_high = candidate.entry_low, candidate.entry_high
    entry = (entry_low + entry_high) / 2.0
    atr = candidate.meta.get("atr14")
    if not isinstance(atr, (int, float)) or float(atr) <= 0:
        return StructurePolicyResult(False, entry_low, entry_high, candidate.stop_loss,
                                    candidate.tp1, candidate.tp2, candidate.tp3, candidate.tp4,
                                    "missing_positive_atr14", "none")
    stop, source = _structure_stop(candidate, entry, float(atr), config)
    if candidate.direction == Direction.LONG and stop >= entry_low:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "structural_stop_not_below_entry", source)
    if candidate.direction == Direction.SHORT and stop <= entry_high:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "structural_stop_not_above_entry", source)
    risk = abs(entry - stop)
    levels = _historical_liquidity(snapshot, candidate.direction, entry, config.liquidity_lookback_bars)
    if not levels:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "no_opposing_liquidity", source)
    nearest = levels[0]
    if candidate.direction == Direction.LONG:
        available = [level for level in levels if level > entry]
        levels = sorted(available)
    else:
        available = [level for level in levels if level < entry]
        levels = sorted(available, reverse=True)
    if not levels:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "no_reachable_opposing_liquidity", source)
    nearest = levels[0]
    if abs(nearest - entry) < risk * config.min_rr_tp2:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "nearest_liquidity_does_not_justify_risk", source)
    # TP1 is the first reaction zone.  Further targets are only retained when
    # a later observed liquidity level exists; no distant theoretical target
    # is manufactured.  TP4 is optional and falls back to TP3 when absent.
    targets = [nearest]
    for level in levels[1:]:
        if abs(level - entry) <= config.max_target_distance_r * risk:
            targets.append(level)
        if len(targets) == 4:
            break
    if candidate.direction == Direction.LONG:
        targets = sorted(targets)
    else:
        targets = sorted(targets, reverse=True)
    if len(targets) < 4 or len(set(targets)) < 4:
        return StructurePolicyResult(False, entry_low, entry_high, stop, candidate.tp1, candidate.tp2,
                                    candidate.tp3, candidate.tp4, "insufficient_distinct_liquidity_targets", source)
    return StructurePolicyResult(True, entry_low, entry_high, stop,
                                targets[0], targets[1], targets[2], targets[3],
                                "structure_and_liquidity_feasible", source)
