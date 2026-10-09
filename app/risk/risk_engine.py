"""Risk engine.

Implements spec section 20's cash-at-stop risk model, and enforces the
daily/concurrency/cooldown/correlated-exposure limits from
config/risk.yaml. Equity is read exclusively via
app.config.get_assumed_account_equity_usd() — this module never
accepts or falls back to a hardcoded equity value; every call site
that needs equity must supply it explicitly, sourced from that one
function.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import RiskLimitExceededError
from app.core.math import round_down_to_step
from app.core.models import CandidateSignal, Direction, MarketSnapshot
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PositionSizeResult:
    qty_raw: float
    qty: float
    notional_usd: float
    r_budget_usd: float


def compute_position_size(
    *,
    assumed_equity_usd: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss: float,
    qty_step: float,
    fee_maker_bps: float,
    fee_taker_bps: float,
    depth_usd: float | None,
) -> PositionSizeResult:
    """Cash-at-stop position sizing (spec section 20), fee-inclusive.

        R_budget      = E * (risk_per_trade_pct / 100)
        loss_per_unit = raw stop distance + round_trip_at_sl
        qty_raw       = R_budget / loss_per_unit
        qty           = round_down(qty_raw, qty_step)

    round_trip_at_sl = maker entry + taker stop-market exit + stop-leg
    slippage (see app/backtest/costs.py for why each leg has its fee
    class). This is what the user actually loses if stopped out, so
    sizing on it keeps the fee-inclusive loss within the R budget.

    Slippage depends on notional, which depends on qty, which depends on
    slippage. This is resolved by fixed-point iteration (bounded, and it
    converges quickly because slippage is a small fraction of the
    stop distance). The final qty is then verified: if the realised
    stop-out loss at that qty still exceeds R_budget (rounding cannot
    make it exceed, but the iteration cap could), qty is reduced one
    step at a time until it does not.

    For USDⓈ-M linear contracts unit_contract_value = 1 and
    quanto_mult = 1.
    """
    from app.backtest.costs import cash_risk_per_unit, compute_cost_breakdown
    from app.core.models import Direction

    if assumed_equity_usd <= 0:
        raise ValueError("assumed_equity_usd must be > 0")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be > 0")
    if qty_step <= 0:
        raise ValueError("qty_step must be > 0")

    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be > 0")

    direction = Direction.LONG if stop_loss < entry_price else Direction.SHORT
    r_budget = assumed_equity_usd * (risk_per_trade_pct / 100.0)

    def loss_per_unit_at(notional: float) -> float:
        cost = compute_cost_breakdown(
            entry_price=entry_price,
            stop_price=stop_loss,
            fee_maker_bps=fee_maker_bps,
            fee_taker_bps=fee_taker_bps,
            notional_usd=notional,
            depth_usd=depth_usd,
        )
        return cash_risk_per_unit(
            direction=direction, entry_price=entry_price, stop_loss=stop_loss, cost=cost
        )

    notional = 0.0
    qty_raw = 0.0
    for _ in range(20):
        qty_raw = r_budget / loss_per_unit_at(notional)
        new_notional = qty_raw * entry_price
        if abs(new_notional - notional) <= 1e-9 * max(1.0, new_notional):
            notional = new_notional
            break
        notional = new_notional

    qty = round_down_to_step(qty_raw, qty_step)

    # Verification pass: realised stop-out loss at the rounded qty must
    # not exceed the R budget. Reduce by whole steps if it does.
    while qty > 0 and qty * loss_per_unit_at(qty * entry_price) > r_budget * (1 + 1e-9):
        qty = round_down_to_step(qty - qty_step, qty_step)

    return PositionSizeResult(
        qty_raw=qty_raw, qty=qty, notional_usd=qty * entry_price, r_budget_usd=r_budget
    )


@dataclass
class OpenSignalRecord:
    signal_id: str
    symbol: str
    direction: Direction
    opened_ts_ms: int


@dataclass
class DailyCounters:
    date_str: str
    signals_emitted: int = 0
    realized_loss_r: float = 0.0


@dataclass
class RiskState:
    """Mutable risk-tracking state carried across evaluation ticks by
    the main loop. Not itself persisted here — main.py is responsible
    for hydrating/persisting this from the database at boot/shutdown.
    """

    open_signals: list[OpenSignalRecord] = field(default_factory=list)
    daily: DailyCounters | None = None
    cooldown_until_ts_ms: dict[str, int] = field(default_factory=dict)  # symbol -> ts_ms


@dataclass
class CandidateDeduplicator:
    """Suppress repeated equivalent candidates before audit/grade work."""

    last_seen: dict[tuple[str, str, str], tuple[int, float]] = field(default_factory=dict)

    def should_suppress(self, candidate: CandidateSignal, *, now_ts_ms: int, cooldown_min: float) -> bool:
        key = (candidate.symbol, candidate.strategy_source, candidate.direction.value)
        previous = self.last_seen.get(key)
        last_ts = previous[0] if previous is not None else None
        duplicate = bool(
            previous is not None
            and 0 <= now_ts_ms - previous[0] < int(cooldown_min * 60_000)
            and abs(candidate.confidence - previous[1]) <= 0.001
        )
        logger.debug(
            "candidate_dedup_check | symbol=%s | direction=%s | strategy=%s | "
            "last_signal_ts=%s | cooldown_min=%s | duplicate=%s",
            candidate.symbol, candidate.direction.value, candidate.strategy_source,
            last_ts, cooldown_min, duplicate,
        )
        if not duplicate:
            self.last_seen[key] = (now_ts_ms, candidate.confidence)
        return duplicate


def check_risk_limits(
    *,
    candidate: CandidateSignal,
    risk_state: RiskState,
    risk_cfg: dict,
    as_of_ts_ms: int,
    current_date_str: str,
) -> list[str]:
    """Returns a list of human-readable violation reasons (empty list =
    all checks passed). Does not mutate risk_state; callers apply state
    changes (e.g. incrementing daily counters) only after a signal is
    actually published.
    """
    violations: list[str] = []

    clusters = risk_cfg.get("correlated_clusters", [])
    occupied_slots = count_concurrent_slots(risk_state.open_signals, clusters)
    candidate_cluster = _cluster_for_symbol(candidate.symbol, clusters)
    candidate_slot_key = candidate_cluster if candidate_cluster is not None else f"__single__:{candidate.symbol}"
    existing_slot_keys = {
        (_cluster_for_symbol(s.symbol, clusters) or f"__single__:{s.symbol}")
        for s in risk_state.open_signals
    }
    # Adding this candidate consumes a NEW slot only if its cluster (or
    # itself, if unclustered) doesn't already occupy one — i.e. a
    # second signal in the same cluster as an already-open one does
    # not push occupied_slots higher.
    would_be_slots = occupied_slots if candidate_slot_key in existing_slot_keys else occupied_slots + 1

    if would_be_slots > risk_cfg["max_concurrent"]:
        violations.append(
            f"max_concurrent limit reached: adding {candidate.symbol} "
            f"(cluster={candidate_cluster!r}) would occupy {would_be_slots} slots, "
            f"limit is {risk_cfg['max_concurrent']}"
        )

    if risk_state.daily is not None and risk_state.daily.date_str == current_date_str:
        if risk_state.daily.signals_emitted >= risk_cfg["max_daily_signals"]:
            violations.append(
                f"max_daily_signals limit reached: {risk_state.daily.signals_emitted} >= {risk_cfg['max_daily_signals']}"
            )
        if risk_state.daily.realized_loss_r >= risk_cfg["max_daily_loss_r"]:
            violations.append(
                f"max_daily_loss_r limit reached: {risk_state.daily.realized_loss_r} >= {risk_cfg['max_daily_loss_r']}"
            )

    cooldown_until = risk_state.cooldown_until_ts_ms.get(candidate.symbol)
    if cooldown_until is not None and as_of_ts_ms < cooldown_until:
        violations.append(
            f"{candidate.symbol} is in cooldown until {cooldown_until} (now {as_of_ts_ms})"
        )

    for open_sig in risk_state.open_signals:
        if open_sig.symbol == candidate.symbol and open_sig.direction != candidate.direction:
            violations.append(
                f"opposite-direction signal already open for {candidate.symbol}: {open_sig.signal_id}"
            )

    return violations


def _cluster_for_symbol(symbol: str, clusters: list[dict]) -> str | None:
    for cluster in clusters:
        if symbol in cluster.get("symbols", []):
            return cluster["name"]
    return None


def count_concurrent_slots(open_signals: list[OpenSignalRecord], clusters: list[dict]) -> int:
    """Counts occupied max_concurrent "slots", where every symbol in a
    correlated cluster (e.g. BTCUSDT/ETHUSDT/SOLUSDT) collectively
    counts as ONE slot, and any symbol not in a cluster is its own
    slot. This is the exact semantics specified for max_concurrent:
    a cluster occupies one slot regardless of how many of its member
    symbols currently have an open signal — a second, third, etc. open
    signal within the SAME cluster does not consume an additional
    slot (they are still individually tracked as separate
    OpenSignalRecords for other purposes, such as the opposite-
    direction check above, which remains per-symbol).

    This replaces an earlier, incorrect implementation that treated
    correlated-cluster capping as a separate, independently-invented
    "half of max_concurrent" rule — that was never specified and has
    been removed in favor of the precise "one slot" semantics given
    explicitly for Batch 4B.
    """
    occupied_slots: set[str] = set()
    for open_sig in open_signals:
        cluster = _cluster_for_symbol(open_sig.symbol, clusters)
        slot_key = cluster if cluster is not None else f"__single__:{open_sig.symbol}"
        occupied_slots.add(slot_key)
    return len(occupied_slots)


def apply_min_rr_gate(rr_tp2: float, min_rr_tp2: float) -> bool:
    """Returns True if the candidate's post-cost R:R at TP2 clears the
    configured minimum. This gate is applied before the candidate is
    even passed to consensus for S5 per STRATEGIES_SPEC.md, and
    universally for all strategies before final signal publication."""
    return rr_tp2 >= min_rr_tp2
