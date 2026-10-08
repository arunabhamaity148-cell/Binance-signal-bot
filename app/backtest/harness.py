"""Backtest harness: reads a JSONL file of historical bars (plus
optional auxiliary data) and replays it through
run_single_symbol_backtest. NO live I/O anywhere in this module — no
REST calls, no WebSocket connections, no network access of any kind.
This is what lets run_backtest.py satisfy spec section 22's
"NEVER downloads data" requirement structurally: the harness simply
has no code path capable of reaching the network.

JSONL FORMAT (one JSON object per line, oldest-first):

    {"ts_ms": 1700000000000, "open": 100.0, "high": 101.0, "low": 99.5,
     "close": 100.5, "volume": 123.4,
     "orderbook": {"best_bid": ..., "best_ask": ..., "bid_depth_5lvl_usd": ...,
                   "ask_depth_5lvl_usd": ...},          # optional
     "taker_flow": {"taker_buy_base_last_bars": [...], "total_volume_last_bars": [...]},  # optional
     "derivatives": {"funding_rate": ..., "open_interest": ..., ...}}  # optional, see
                                                                         # _parse_derivatives_row

A row missing "orderbook"/"taker_flow"/"derivatives" simply leaves
those fields absent for that bar's index — exactly mirroring what
run_single_symbol_backtest already does with its optional
*_at_index maps (and what the live strategies' fail-closed conditions
already handle correctly when that data is genuinely unavailable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import DataIntegrityError
from app.core.math import OHLC
from app.core.models import OrderBookState, TakerFlowState


@dataclass(frozen=True)
class ReplayData:
    bars: list[OHLC]
    orderbook_at_index: dict[int, OrderBookState]
    taker_flow_at_index: dict[int, TakerFlowState]
    # derivatives_at_index intentionally omitted here: DerivativesState
    # requires several historical SERIES (funding/OI/ratio history),
    # not a single-row snapshot, so it cannot be reconstructed
    # faithfully from one JSONL row in isolation — see
    # `load_derivatives_series_jsonl` below for the series-oriented
    # loader this harness expects callers to use alongside this one for
    # strategies/guards that need derivatives data.


def load_bars_jsonl(path: str | Path, *, symbol: str) -> ReplayData:
    """Reads a JSONL file, one row per bar, oldest-first. Raises
    DataIntegrityError on out-of-order timestamps, malformed JSON, or a
    row missing a required OHLCV field — fail-closed on malformed
    historical data exactly as the live normalization layer does for
    live data (app/data/normalization.py).

    This function performs NO network access and NO file writes; it
    only reads the single local file at `path`.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"backtest data file not found: {p}")

    bars: list[OHLC] = []
    orderbook_at_index: dict[int, OrderBookState] = {}
    taker_flow_at_index: dict[int, TakerFlowState] = {}

    prev_ts: int | None = None
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataIntegrityError(f"{p}:{lineno}: malformed JSON: {exc}") from exc

            required = ("ts_ms", "open", "high", "low", "close", "volume")
            missing = [f for f in required if f not in row]
            if missing:
                raise DataIntegrityError(f"{p}:{lineno}: row missing required fields {missing}")

            ts_ms = int(row["ts_ms"])
            if prev_ts is not None and ts_ms <= prev_ts:
                raise DataIntegrityError(
                    f"{p}:{lineno}: out-of-order or duplicate ts_ms {ts_ms} <= previous {prev_ts}"
                )
            prev_ts = ts_ms

            try:
                bar = OHLC(
                    open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                    close=float(row["close"]), volume=float(row["volume"]), close_time_ms=ts_ms,
                )
            except ValueError as exc:
                raise DataIntegrityError(f"{p}:{lineno}: invalid OHLC values: {exc}") from exc

            index = len(bars)
            bars.append(bar)

            if "orderbook" in row:
                ob_row = row["orderbook"]
                ob_required = ("best_bid", "best_ask", "bid_depth_5lvl_usd", "ask_depth_5lvl_usd")
                ob_missing = [f for f in ob_required if f not in ob_row]
                if ob_missing:
                    raise DataIntegrityError(f"{p}:{lineno}: orderbook row missing fields {ob_missing}")
                orderbook_at_index[index] = OrderBookState(
                    symbol=symbol, best_bid=float(ob_row["best_bid"]), best_ask=float(ob_row["best_ask"]),
                    bid_depth_5lvl_usd=float(ob_row["bid_depth_5lvl_usd"]),
                    ask_depth_5lvl_usd=float(ob_row["ask_depth_5lvl_usd"]),
                    event_ts_ms=ts_ms, received_ts_ms=ts_ms,
                )

            if "taker_flow" in row:
                tf_row = row["taker_flow"]
                tf_required = ("taker_buy_base_last_bars", "total_volume_last_bars")
                tf_missing = [f for f in tf_required if f not in tf_row]
                if tf_missing:
                    raise DataIntegrityError(f"{p}:{lineno}: taker_flow row missing fields {tf_missing}")
                taker_flow_at_index[index] = TakerFlowState(
                    symbol=symbol,
                    taker_buy_base_last_bars=[float(v) for v in tf_row["taker_buy_base_last_bars"]],
                    total_volume_last_bars=[float(v) for v in tf_row["total_volume_last_bars"]],
                    event_ts_ms=ts_ms, received_ts_ms=ts_ms,
                )

    if not bars:
        raise DataIntegrityError(f"{p}: no valid bar rows found")

    return ReplayData(bars=bars, orderbook_at_index=orderbook_at_index, taker_flow_at_index=taker_flow_at_index)


def write_bars_jsonl(path: str | Path, bars: list[OHLC]) -> None:
    """Writes a list of OHLC bars to a JSONL file in this harness's
    format (OHLCV fields only — auxiliary data must be added
    separately if needed). Used by tests to construct fixtures and by
    an operator wanting to convert their own historical data into this
    format; NEVER called by any code path that could instead fetch
    live data (no such call exists anywhere in this module)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for bar in bars:
            row = {
                "ts_ms": bar.close_time_ms, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
            }
            fh.write(json.dumps(row) + "\n")
