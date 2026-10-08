"""Error taxonomy for the signal-only bot.

Every exception type here is deliberate and specific. Generic
`Exception` is never raised directly in application code so that
callers (especially the veto engine, which must treat any unexpected
exception as a BLOCK per spec section 12) can distinguish expected,
handled failure modes from truly unexpected bugs.
"""

from __future__ import annotations


class SignalBotError(Exception):
    """Base class for all application-raised errors."""


# ---------------------------------------------------------------------------
# Boot / configuration errors
# ---------------------------------------------------------------------------


class ConfigError(SignalBotError):
    """A configuration file is missing, malformed, or fails validation."""


class MissingAssumedEquityError(SignalBotError):
    """ASSUMED_ACCOUNT_EQUITY_USD is missing, non-numeric, or <= 0.

    Per spec section 18 and section 20: the bot must refuse to start.
    There is no hardcoded fallback anywhere in the codebase.
    """


class TradingCredentialsPresentError(SignalBotError):
    """A forbidden exchange trading credential was found in the environment.

    Per spec section 25: if exchange trading credentials exist in the
    environment, the bot MUST abort at boot.
    """


class UniverseValidationError(SignalBotError):
    """Fewer than the minimum required symbols validated against
    /fapi/v1/exchangeInfo at boot (spec section 4)."""


# ---------------------------------------------------------------------------
# Data-layer errors
# ---------------------------------------------------------------------------


class DataIntegrityError(SignalBotError):
    """Malformed, out-of-order, duplicate, or otherwise structurally
    invalid market data was encountered (maps to guard G1)."""


class StaleDataError(SignalBotError):
    """Data exceeded its configured staleness budget."""


class FeedHealthError(SignalBotError):
    """WebSocket feed gap, reconnect storm, or other feed-health failure
    (maps to guard G2)."""


class SnapshotIncompleteError(SignalBotError):
    """A MarketSnapshot was requested before all required inputs for the
    requesting component were available."""


class RestClientError(SignalBotError):
    """Non-timeout, non-rate-limit REST failure (e.g. malformed response)."""


class RestTimeoutError(SignalBotError):
    """A REST call exceeded its configured timeout."""


class RestRateLimitError(SignalBotError):
    """A REST call received an HTTP 429. Carries retry_after_s when the
    exchange supplied one."""

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class WebSocketDisconnectedError(SignalBotError):
    """The WebSocket connection dropped and a reconnect is required."""


# ---------------------------------------------------------------------------
# News errors
# ---------------------------------------------------------------------------


class NewsSourceUnavailableError(SignalBotError):
    """A single news source failed to fetch or parse. Non-fatal at the
    system level; the news engine degrades that source only."""


class NewsCategoryUnavailableError(SignalBotError):
    """An entire news category has insufficient healthy sources to
    support corroboration. Fails closed for that category only."""


# ---------------------------------------------------------------------------
# Strategy / signal errors
# ---------------------------------------------------------------------------


class StrategyEvaluationError(SignalBotError):
    """Unexpected failure inside a strategy's pure evaluation function.
    Strategies must return [] for expected missing-data conditions;
    this is reserved for genuine bugs, and is treated as a BLOCK."""


class SignalConstructionError(SignalBotError):
    """A Signal failed its construction-time validators (see
    SIGNAL_MODEL.md invariants). Treated as a G12-class failure."""


class GuardExecutionError(SignalBotError):
    """A veto guard raised an unexpected exception. The veto engine
    converts this into a BLOCK result; this exception type exists so
    that conversion is explicit and auditable rather than a bare except
    swallowing arbitrary exceptions."""

    def __init__(self, guard_name: str, original: BaseException) -> None:
        super().__init__(f"guard_exception:{guard_name}: {original!r}")
        self.guard_name = guard_name
        self.original = original


# ---------------------------------------------------------------------------
# Risk / sizing errors
# ---------------------------------------------------------------------------


class RiskLimitExceededError(SignalBotError):
    """A daily/concurrency/cooldown/correlation risk limit was hit."""


class InvalidTickAlignmentError(SignalBotError):
    """A computed price does not align to the symbol's price_tick, or a
    computed quantity does not align to qty_step."""


# ---------------------------------------------------------------------------
# Telegram / delivery errors
# ---------------------------------------------------------------------------


class TelegramDeliveryError(SignalBotError):
    """Telegram send failed. Must never propagate to break the
    market-data engine (spec section 15)."""


class TelegramRateLimitError(TelegramDeliveryError):
    """Telegram responded 429. Carries retry_after_s."""

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


# ---------------------------------------------------------------------------
# Persistence errors
# ---------------------------------------------------------------------------


class DatabaseWriteError(SignalBotError):
    """SQLite write failed (disk full, locked, etc.)."""


class MigrationError(SignalBotError):
    """A database migration failed to apply."""
