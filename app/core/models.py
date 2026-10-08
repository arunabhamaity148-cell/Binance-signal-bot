"""Shared, immutable data models used across data/, news/, strategies/,
risk/, and signals/.

These are the contracts that let strategies and guards remain pure
functions: every one of them takes a MarketSnapshot (plus NewsState
and config) and returns a value, with no hidden I/O or shared mutable
state. All dataclasses here are frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.math import OHLC


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Grade(str, Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"


class VetoState(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class GuardSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class GuardAction(str, Enum):
    BLOCK = "BLOCK"
    DEGRADE = "DEGRADE"
    PASS = "PASS"


class NewsCategory(str, Enum):
    REGULATORY = "regulatory"
    MACRO = "macro"
    HACK = "hack"
    LISTING = "listing"
    DELISTING = "delisting"
    ETF = "etf"
    LIQUIDATION = "liquidation"
    OUTAGE = "outage"
    OTHER = "other"


class NewsDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class NewsSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ChannelName(str, Enum):
    PRICE_STRUCTURE = "PRICE_STRUCTURE"
    LIQUIDITY = "LIQUIDITY"
    TAKER_FLOW = "TAKER_FLOW"
    OI = "OI"
    VOLATILITY = "VOLATILITY"
    FUNDING = "FUNDING"


# ---------------------------------------------------------------------------
# Derivatives / orderbook state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimestampedValue:
    value: float
    event_ts_ms: int
    received_ts_ms: int


@dataclass(frozen=True)
class OrderBookState:
    symbol: str
    best_bid: float
    best_ask: float
    bid_depth_5lvl_usd: float
    ask_depth_5lvl_usd: float
    event_ts_ms: int
    received_ts_ms: int

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        if self.mid == 0:
            raise ValueError("mid price is zero, cannot compute spread")
        return (self.best_ask - self.best_bid) / self.mid * 10_000

    @property
    def is_crossed(self) -> bool:
        return self.best_bid >= self.best_ask

    @property
    def is_empty(self) -> bool:
        return self.bid_depth_5lvl_usd <= 0 or self.ask_depth_5lvl_usd <= 0


@dataclass(frozen=True)
class TakerFlowState:
    symbol: str
    taker_buy_base_last_bars: list[float]
    total_volume_last_bars: list[float]
    event_ts_ms: int
    received_ts_ms: int

    @property
    def taker_buy_ratio(self) -> float:
        total = sum(self.total_volume_last_bars)
        if total == 0:
            raise ValueError("total volume is zero, cannot compute taker buy ratio")
        return sum(self.taker_buy_base_last_bars) / total


@dataclass(frozen=True)
class DerivativesState:
    """Funding, OI, and long/short ratio series for one symbol.

    Series are ordered oldest-first, closed samples only.
    """

    symbol: str
    funding_rate_history: list[TimestampedValue]
    open_interest_history_5m: list[TimestampedValue]
    open_interest_history_15m: list[TimestampedValue]
    open_interest_history_1h: list[TimestampedValue]
    open_interest_history_1d: list[TimestampedValue]
    long_short_account_ratio_history: list[TimestampedValue]
    taker_long_short_ratio_history: list[TimestampedValue]
    premium_index_current: TimestampedValue | None


@dataclass(frozen=True)
class SymbolKlines:
    """Closed klines for one symbol, one timeframe."""

    symbol: str
    timeframe: str
    bars: list[OHLC]  # oldest-first, closed bars only


@dataclass(frozen=True)
class FeedHealth:
    symbol: str
    stream: str
    last_message_received_ts_ms: int
    reconnect_count_window: int
    is_connected: bool


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsEvent:
    event_id: str
    source_name: str
    tier: int  # 1, 2, or 3
    canonical_url: str
    domain: str
    published_ts_ms: int
    fetched_ts_ms: int
    receipt_ts_ms: int
    category: NewsCategory
    direction: NewsDirection
    entities: tuple[str, ...]  # symbols or "MARKET" for market-wide
    credibility_weight: float
    confidence: float
    severity: NewsSeverity
    content_hash: str


@dataclass(frozen=True)
class NewsState:
    """Aggregated, decayed, corroborated news state as of a point in time."""

    as_of_ts_ms: int
    active_events: tuple[NewsEvent, ...]
    unhealthy_categories: frozenset[NewsCategory] = field(default_factory=frozenset)

    def active_for(self, symbol: str) -> tuple[NewsEvent, ...]:
        return tuple(
            e for e in self.active_events if symbol in e.entities or "MARKET" in e.entities
        )

    def max_severity_for(self, symbol: str) -> NewsSeverity:
        events = self.active_for(symbol)
        if not events:
            return NewsSeverity.LOW
        order = [NewsSeverity.LOW, NewsSeverity.MEDIUM, NewsSeverity.HIGH, NewsSeverity.CRITICAL]
        return max(events, key=lambda e: order.index(e.severity)).severity


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketSnapshot:
    """Immutable, versioned snapshot of everything a strategy or guard
    may read. Exactly one instance is built per symbol per evaluation
    tick; every strategy and every guard reads from the SAME instance.
    """

    snapshot_version: str
    symbol: str
    as_of_ts_ms: int
    klines: dict[str, SymbolKlines]  # keyed by timeframe: "5m","15m","1h","4h","1d"
    orderbook: OrderBookState | None
    taker_flow: TakerFlowState | None
    derivatives: DerivativesState | None
    feed_health: dict[str, FeedHealth]  # keyed by stream name
    price_tick: float
    qty_step: float
    min_qty: float
    fee_maker_bps: float
    fee_taker_bps: float

    def klines_for(self, timeframe: str) -> list[OHLC]:
        sk = self.klines.get(timeframe)
        if sk is None:
            return []
        return sk.bars


# ---------------------------------------------------------------------------
# Guard results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardResult:
    guard_name: str
    passed: bool
    severity: GuardSeverity
    action: GuardAction
    reason: str | None = None
    degrade_max_grade: str | None = None


@dataclass(frozen=True)
class VetoOutcome:
    veto_state: VetoState
    veto_reason: str | None
    max_grade_cap: str | None  # None means no cap; else "A+"|"A"|"B"
    guard_results: tuple[GuardResult, ...]


# ---------------------------------------------------------------------------
# Candidate signal (pre-consensus, pre-veto)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateSignal:
    """A strategy's raw proposal, before consensus grading and veto
    evaluation. Never sent anywhere; always passes through
    risk/consensus.py and risk/veto_engine.py before becoming a
    Signal."""

    symbol: str
    direction: Direction
    strategy_source: str  # "S1".."S5"
    confidence: float  # strategy's own local confidence, 0-1
    channels: tuple[ChannelName, ...]  # canonical channels this candidate uses
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    why_lines: tuple[str, ...]
    meta: dict[str, object]  # audit trail: atr14, snapshot_version, thresholds used, etc.
    event_ts_ms: int
