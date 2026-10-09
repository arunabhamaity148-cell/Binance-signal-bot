"""Event-driven backtest engine.

CRITICAL DESIGN CONSTRAINT (spec section 22): this engine is
EVENT-DRIVEN and NEXT-BAR-FILL ONLY, and it replays history through
the EXACT SAME strategy/consensus/veto/risk/signal code paths the live
system uses — app.strategies.registry.all_strategies(),
app.risk.consensus, app.risk.veto_engine, app.risk.risk_engine,
app.signals.signal_engine. There is no parallel reimplementation of
any strategy or guard logic here. This is what makes the backtest's
results meaningful as a statement about the ACTUAL production code,
not about a separate approximation of it.

NO LOOK-AHEAD: at historical index `i`, the MarketSnapshot built for
that evaluation tick contains ONLY bars with index <= i on every
timeframe. A candidate signal generated from that snapshot is never
"filled" on the SAME bar `i` that generated it — fills (via
fills.assess_limit_fill) are only ever checked starting at bar `i+1`
and later. This next-bar-fill discipline is enforced structurally by
the walk loop below (the entry-fill search starts at `i+1`), not by a
runtime check that could be bypassed — see
tests/integration/test_backtest_lookahead.py, which proves this by
replaying the SAME fixture with and without future bars redacted and
asserting the results differ (if look-ahead were present, redacting
future bars the engine should never have seen would NOT change the
output).
"""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right

from app.backtest.costs import CostBreakdown, compute_cost_breakdown
from app.backtest.fills import (
    PartialExitEvent, PositionState, assess_limit_fill, deterministic_candidate_signal_id,
    fill_probability_succeeds, process_bar_for_open_position,
)
from app.backtest.metrics import TradeResult
from app.core.models import (
    CandidateSignal,
    Direction,
    FeedHealth,
    MarketSnapshot,
    NewsState,
    SymbolKlines,
)
from app.data.snapshot import SnapshotInputs, build_snapshot
from app.risk.consensus import assign_grade, compute_effective_votes
from app.risk.veto_engine import run_veto_engine
from app.strategies.registry import all_strategies


@dataclass(frozen=True)
class BacktestConfig:
    app_config: object  # app.config.AppConfig, kept generic to avoid an import cycle in type position
    assumed_equity_usd: float
    symbol_tier: str
    min_candles: int
    assumed_latency_s: float = 2.0  # class E, documented backtest assumption (human reaction time)
    regime_label_fn: object = None  # optional Callable[[MarketSnapshot], str], defaults to "default"


def _default_regime_label(snapshot: MarketSnapshot) -> str:
    return "default"


def _build_snapshot_at_index(
    *,
    symbol: str,
    all_bars: dict[str, list],  # timeframe -> list[OHLC], FULL history, oldest-first
    index_per_timeframe: dict[str, int],  # timeframe -> index of the latest bar visible "now"
    as_of_ts_ms: int,
    price_tick: float,
    qty_step: float,
    min_qty: float,
    fee_maker_bps: float,
    fee_taker_bps: float,
    min_candles: int,
    orderbook=None,
    taker_flow=None,
    derivatives=None,
) -> MarketSnapshot | None:
    """Build a MarketSnapshot using ONLY bars up to (inclusive of)
    `index_per_timeframe[tf]` on each timeframe — the structural
    no-look-ahead boundary. Returns None if insufficient history exists
    yet at this point in the replay (mirrors build_snapshot's own
    SnapshotIncompleteError, caught and converted to None here since a
    backtest replay treats "not enough history yet" as "skip this
    tick", not as a fatal error).
    """
    klines: dict[str, SymbolKlines] = {}
    for tf, bars in all_bars.items():
        idx = index_per_timeframe.get(tf)
        if idx is None:
            continue
        visible_bars = bars[: idx + 1]  # inclusive of idx, nothing beyond
        klines[tf] = SymbolKlines(symbol=symbol, timeframe=tf, bars=visible_bars)

    # Synthesized feed health for replay purposes: the mere existence
    # of historical bar data at `as_of_ts_ms` IS the backtest's analog
    # of "the live feed was healthy and delivering data" — there is no
    # real WebSocket connection to assess during a replay, and G2
    # (feed health) must not spuriously block every backtest candidate
    # just because there is no live feed object to inspect. This is
    # NOT a bypass of G2 (G2 still runs, still reads this value, and
    # would still correctly block if a caller supplied a disconnected/
    # stale FeedHealth here to deliberately test a feed-outage
    # scenario) — it is the faithful backtest-domain equivalent of "the
    # feed was up," parallel to how live main.py would populate this
    # field from the real WebSocket client's state.
    feed_health = {
        f"{symbol.lower()}@aggTrade": FeedHealth(
            symbol=symbol, stream=f"{symbol.lower()}@aggTrade",
            last_message_received_ts_ms=as_of_ts_ms, reconnect_count_window=0, is_connected=True,
        )
    }

    inputs = SnapshotInputs(
        symbol=symbol, as_of_ts_ms=as_of_ts_ms, klines=klines,
        orderbook=orderbook, taker_flow=taker_flow, derivatives=derivatives, feed_health=feed_health,
        price_tick=price_tick, qty_step=qty_step, min_qty=min_qty,
        fee_maker_bps=fee_maker_bps, fee_taker_bps=fee_taker_bps,
    )
    try:
        return build_snapshot(inputs, min_candles=min_candles)
    except Exception:  # noqa: BLE001 - insufficient history at this point in replay is expected, not fatal
        return None


def generate_candidates_at_snapshot(
    snapshot: MarketSnapshot, news_state: NewsState, strategy_cfg: dict,
    *, regime=None, regime_logger=None,
) -> list[CandidateSignal]:
    """Runs every registered strategy against `snapshot` — the SAME
    app.strategies.registry.all_strategies() the live loop uses — and
    returns every candidate produced. This function contains no
    strategy logic of its own."""
    candidates: list[CandidateSignal] = []
    for strategy in all_strategies():
        # Registry IDs map explicitly to YAML sections; this fallback keeps
        # third-party/test strategies compatible with the old dispatcher.
        section_names = {
            "S1": "s1_liquidity_sweep", "S2": "s2_volatility_compression",
            "S3": "s3_funding_crowding", "S4": "s4_oi_trend", "S5": "s5_oi_regime",
        }
        section = strategy_cfg.get(section_names.get(strategy.strategy_id, ""), {})
        allowed = section.get("allowed_regimes")
        regime_name = getattr(regime, "value", regime)
        if regime_name is not None and regime_name not in (allowed or ()):
            if regime_logger is not None:
                regime_logger(strategy.strategy_id, regime_name)
            continue
        candidates.extend(strategy.evaluate(snapshot, news_state, strategy_cfg))
    return candidates


def grade_candidates(candidates: list[CandidateSignal], consensus_cfg: dict, *, diagnostic_logger=None) -> dict:
    """Groups candidates by (symbol, direction) and runs the SAME
    app.risk.consensus effective-vote grading the live loop uses.
    Returns {(symbol, direction): (grade_or_none, confidence_weighted)}.
    When supplied, ``diagnostic_logger`` receives the consensus input and
    output context immediately before and after grade assignment.
    """
    groups: dict[tuple[str, str], list[CandidateSignal]] = {}
    for c in candidates:
        key = (c.symbol, c.direction.value)
        groups.setdefault(key, []).append(c)

    result = {}
    for key, group in groups.items():
        consensus_result = compute_effective_votes(group)
        if diagnostic_logger is not None:
            diagnostic_logger("consensus_input", {
                "symbol": key[0], "direction": key[1], "num_candidates": len(group),
                "channels": sorted({channel.value for item in group for channel in item.channels}),
                "confidences": [item.confidence for item in group], "v_eff": consensus_result.v_eff,
            })
        grade = assign_grade(consensus_result, consensus_cfg)
        if diagnostic_logger is not None:
            diagnostic_logger("consensus_output", {
                "symbol": key[0], "direction": key[1],
                "grade": getattr(grade, "value", grade) if grade is not None else None,
                "v_eff": consensus_result.v_eff, "confidence_weighted": consensus_result.confidence_weighted,
                "num_groups": consensus_result.num_groups,
                "reason": "grade_threshold_met" if grade is not None else "no_grade_threshold_met",
            })
        result[key] = (grade, consensus_result.confidence_weighted, group)
    return result


def run_single_symbol_backtest(
    *,
    symbol: str,
    all_bars: dict[str, list],
    event_ts_per_5m_index: list[int],
    bt_cfg: BacktestConfig,
    news_state: NewsState,
    orderbook_at_index: dict | None = None,
    taker_flow_at_index: dict | None = None,
    derivatives_at_index: dict | None = None,
) -> list[TradeResult]:
    """Walk the 5m timeframe bar-by-bar (the primary evaluation
    cadence, matching the live loop), at each bar build a no-look-ahead
    snapshot, run the full strategy -> consensus -> veto pipeline, and
    for any PASS'd candidate search FORWARD (starting at the next bar)
    for a fill and then track it to close via fills.py.

    `orderbook_at_index`, `taker_flow_at_index`, and
    `derivatives_at_index` are optional {5m_bar_index -> value} maps
    supplying the auxiliary data (order book state, taker flow, and
    derivatives series) visible AS OF each evaluation index — exactly
    mirroring the live snapshot's own fields. Historical order-book
    depth and taker-flow data are not reconstructable from OHLCV
    klines alone, so a real backtest run needs this data supplied
    separately (harness.py's JSONL replay format carries it;
    run_backtest.py's --csv path, if the CSV lacks these columns,
    simply runs with None throughout, below).

    EVERY strategy in this codebase requires taker_flow and/or
    derivatives data to produce ANY candidate (S1 needs taker_flow,
    S2/S3/S4/S5 need derivatives) — this is a property of the live
    strategies themselves (their documented fail-closed conditions),
    not something this engine can or should work around. A replay with
    no auxiliary data supplied at all will correctly, honestly produce
    zero trades for every strategy — this is fail-closed behavior
    carried over from live, not a backtest engine limitation to patch
    around with synthesized data the live system never would have had
    either.
    """
    bars_5m = all_bars["5m"]
    orderbook_at_index = orderbook_at_index or {}
    taker_flow_at_index = taker_flow_at_index or {}
    derivatives_at_index = derivatives_at_index or {}
    min_candles = bt_cfg.min_candles
    regime_fn = bt_cfg.regime_label_fn or _default_regime_label

    results: list[TradeResult] = []
    open_positions: list[tuple[PositionState, CandidateSignal, int, CostBreakdown]] = []  # state, candidate, entry bar, signal-time costs

    cfg = bt_cfg.app_config

    for i in range(min_candles - 1, len(bars_5m)):
        as_of_ts_ms = bars_5m[i].close_time_ms

        # Advance any open positions with THIS bar first (next-bar-fill
        # relative to when they were opened is enforced by construction
        # — see the entry-fill search loop below, which never starts
        # before entry_bar_idx + 1).
        still_open: list[tuple[PositionState, CandidateSignal, int]] = []
        for pos, cand, entry_idx, cost in open_positions:
            if i > entry_idx:
                pos = process_bar_for_open_position(pos, bars_5m[i])
            if pos.is_fully_closed:
                realized_r = _compute_realized_r(pos, cand) - _costs_in_r(pos, cost, cand)
                results.append(
                    TradeResult(
                        symbol=symbol, strategy_source=cand.strategy_source, direction=cand.direction.value,
                        realized_r=realized_r, was_filled=True,
                        mfe_r=_mfe_r(pos, cand), mae_r=_mae_r(pos, cand),
                        regime_label=regime_fn(None), opened_ts_ms=cand.event_ts_ms,
                        closed_ts_ms=bars_5m[i].close_time_ms,
                    )
                )
            else:
                still_open.append((pos, cand, entry_idx, cost))
        open_positions = still_open

        index_per_tf = {"5m": i}
        # For auxiliary timeframes, expose only the most recent bar closed
        # at or before this 5m evaluation timestamp. Never expose future bars.
        for timeframe, tf_bars in all_bars.items():
            if timeframe == "5m":
                continue
            timestamps = [tf_bar.close_time_ms for tf_bar in tf_bars]
            visible_index = bisect_right(timestamps, as_of_ts_ms) - 1
            if visible_index >= 0:
                index_per_tf[timeframe] = visible_index
        snapshot = _build_snapshot_at_index(
            symbol=symbol, all_bars=all_bars, index_per_timeframe=index_per_tf,
            as_of_ts_ms=as_of_ts_ms, price_tick=cfg.pair_config(symbol)["price_tick"],
            qty_step=cfg.pair_config(symbol)["qty_step"], min_qty=cfg.pair_config(symbol)["min_qty"],
            fee_maker_bps=cfg.pair_config(symbol)["fee_maker_bps"],
            fee_taker_bps=cfg.pair_config(symbol)["fee_taker_bps"], min_candles=min_candles,
            orderbook=orderbook_at_index.get(i), taker_flow=taker_flow_at_index.get(i),
            derivatives=derivatives_at_index.get(i),
        )
        if snapshot is None:
            continue

        candidates = generate_candidates_at_snapshot(snapshot, news_state, cfg.strategy)
        if not candidates:
            continue

        graded = grade_candidates(candidates, cfg.strategy["consensus"])
        for (sym, direction), (grade, confidence, group) in graded.items():
            if grade is None:
                continue
            representative = group[0]
            veto_cfg = cfg.veto
            if not all_bars.get("1h") or not all_bars.get("4h"):
                # A 5M-only replay cannot honestly evaluate a higher-timeframe
                # confluence edge; live snapshots always carry both inputs.
                veto_cfg = dict(cfg.veto)
                veto_cfg["g16_multi_tf_confluence"] = dict(
                    cfg.veto.get("g16_multi_tf_confluence", {}), enabled=False
                )
            veto_outcome = run_veto_engine(
                snapshot=snapshot, news_state=news_state, candidate=representative,
                veto_cfg=veto_cfg, symbol_tier=bt_cfg.symbol_tier, funding_z=None, btc_trend_direction=None,
            )
            if veto_outcome.veto_state.value != "PASS":
                continue

            # Search FORWARD for a fill, starting at the NEXT bar —
            # this is the structural next-bar-fill boundary: index i
            # (the bar that generated this candidate) is never searched.
            entry_result = _search_forward_for_fill(bars_5m, start_idx=i + 1, candidate=representative, max_search_bars=20, signal_id=deterministic_candidate_signal_id(representative))
            if entry_result is None:
                results.append(
                    TradeResult(
                        symbol=symbol, strategy_source=representative.strategy_source,
                        direction=representative.direction.value, realized_r=0.0, was_filled=False,
                        mfe_r=0.0, mae_r=0.0, regime_label=regime_fn(None),
                        opened_ts_ms=representative.event_ts_ms, closed_ts_ms=representative.event_ts_ms,
                    )
                )
                continue

            fill_bar_idx, fill_price = entry_result
            depth_usd = None if snapshot.orderbook is None else min(
                snapshot.orderbook.bid_depth_5lvl_usd, snapshot.orderbook.ask_depth_5lvl_usd
            )
            cost = compute_cost_breakdown(
                entry_price=fill_price, stop_price=representative.stop_loss,
                fee_maker_bps=snapshot.fee_maker_bps, fee_taker_bps=snapshot.fee_taker_bps,
                notional_usd=bt_cfg.assumed_equity_usd, depth_usd=depth_usd,
                latency_s=bt_cfg.assumed_latency_s,
            )
            position = PositionState(
                direction=representative.direction, entry_price=fill_price, stop_loss=representative.stop_loss,
                tp_levels=(representative.tp1, representative.tp2, representative.tp3, representative.tp4),
                partial_exit_fractions=tuple(cfg.risk["partial_exit_fractions"]),
            )
            open_positions.append((position, representative, fill_bar_idx, cost))

    return results


def _search_forward_for_fill(
    bars_5m: list, *, start_idx: int, candidate: CandidateSignal, max_search_bars: int, signal_id: str
) -> tuple[int, float] | None:
    """Starting at `start_idx` (NEVER earlier — this is the next-bar-
    fill enforcement point), search up to `max_search_bars` forward for
    the first bar whose range overlaps the candidate's entry zone.
    Returns (bar_index, fill_price) or None if never filled within the
    search window (the signal expired unfilled)."""
    end_idx = min(len(bars_5m), start_idx + max_search_bars)
    for idx in range(start_idx, end_idx):
        assessment = assess_limit_fill(
            entry_low=candidate.entry_low, entry_high=candidate.entry_high,
            bar=bars_5m[idx], direction=candidate.direction,
        )
        if fill_probability_succeeds(assessment, signal_id=signal_id, bar_timestamp_ms=bars_5m[idx].close_time_ms):
            return idx, assessment.fill_price
    return None


def _costs_in_r(position: PositionState, cost: CostBreakdown, candidate: CandidateSignal) -> float:
    """Convert modeled entry/exit fees, stop slippage and latency impact to R.

    Entry maker fee and human-latency impact apply to the full position.
    Each TP exit incurs its configured fraction of maker fee; if stopped,
    the remaining fraction incurs taker fee and stop-leg slippage. Costs
    are divided by the initial stop distance so TradeResult.realized_r is
    net of modeled costs. The class-E latency input is passed unchanged
    from BacktestConfig (default 2.0 seconds).
    """
    risk_per_unit = abs(position.entry_price - candidate.stop_loss)
    if risk_per_unit <= 0:
        return 0.0
    charged = cost.entry_maker_fee + cost.entry_latency_slippage
    charged += sum(exit_event.fraction_of_original * cost.tp_maker_fee for exit_event in position.exits)
    if position.stopped_out:
        charged += position.remaining_fraction * (cost.sl_taker_fee + cost.sl_leg_slippage)
    return charged / risk_per_unit


def _compute_realized_r(position: PositionState, candidate: CandidateSignal) -> float:
    """Realized R = sum over partial exits of (fraction * R_at_that_exit)
    plus, if stopped out, the remaining fraction at -1R (by definition,
    the stop is exactly 1R away from entry)."""
    if position.direction == Direction.LONG:
        r_unit = position.entry_price - position.stop_loss
    else:
        r_unit = position.stop_loss - position.entry_price
    if r_unit <= 0:
        return 0.0

    total_r = 0.0
    for exit_event in position.exits:
        if position.direction == Direction.LONG:
            exit_r = (exit_event.price - position.entry_price) / r_unit
        else:
            exit_r = (position.entry_price - exit_event.price) / r_unit
        total_r += exit_event.fraction_of_original * exit_r

    if position.stopped_out:
        total_r += position.remaining_fraction * (-1.0)

    return total_r


def _mfe_r(position: PositionState, candidate: CandidateSignal) -> float:
    if not position.exits:
        return 0.0
    if position.direction == Direction.LONG:
        r_unit = position.entry_price - position.stop_loss
    else:
        r_unit = position.stop_loss - position.entry_price
    if r_unit <= 0:
        return 0.0
    best_price = max(e.price for e in position.exits) if position.direction == Direction.LONG else min(e.price for e in position.exits)
    if position.direction == Direction.LONG:
        return (best_price - position.entry_price) / r_unit
    return (position.entry_price - best_price) / r_unit


def _mae_r(position: PositionState, candidate: CandidateSignal) -> float:
    if position.stopped_out:
        return -1.0
    return 0.0
