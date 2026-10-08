"""Runtime enforcement of the signal-only invariant.

This module is the boot-time counterpart to
scripts/scan_signal_only.py: the scanner enforces the invariant
statically (no forbidden code exists in the repository); this module
enforces it dynamically (no forbidden credential exists in the
environment at runtime, and no forbidden capability can be invoked
even by accident, since no such function is ever defined or imported).

There is no code path anywhere in this application that places,
cancels, or modifies an order, sets leverage, or moves funds. This
module's `assert_no_trading_credentials()` is called once at boot and
aborts the process if any forbidden credential is present, which is
the last line of defense against accidentally deploying this signal
bot against a wallet that also holds trading permissions.
"""

from __future__ import annotations

import os

from app.core.errors import TradingCredentialsPresentError

FORBIDDEN_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
)

FORBIDDEN_CAPABILITIES: tuple[str, ...] = (
    "place_order",
    "cancel_order",
    "modify_order",
    "close_position",
    "set_leverage",
    "withdraw",
    "transfer",
)


def assert_no_trading_credentials() -> None:
    """Abort boot if any forbidden exchange trading credential is
    present in the environment (spec section 25).

    This function does not attempt to validate or use the credential
    in any way — its mere presence is disqualifying, since this
    process should never hold trading-capable secrets at all.
    """
    present = [name for name in FORBIDDEN_CREDENTIAL_ENV_VARS if os.environ.get(name)]
    if present:
        raise TradingCredentialsPresentError(
            "Forbidden exchange trading credentials are present in the "
            f"environment: {present}. This is a signal-only bot and must "
            "never hold trading-capable credentials. Refusing to start."
        )


def assert_capability_not_implemented(name: str) -> None:
    """Defensive helper: if any code path ever constructs a capability
    name matching the forbidden list dynamically (e.g. via getattr),
    this raises immediately rather than allowing it to proceed. Used
    by monitoring/health checks that want to affirmatively prove the
    invariant at runtime, not just at import time."""
    if name in FORBIDDEN_CAPABILITIES:
        raise TradingCredentialsPresentError(
            f"Attempted to invoke forbidden capability: {name!r}"
        )


def run_all_boot_safety_checks() -> None:
    """Single entry point called from main.py before anything else
    happens. If this raises, the process must exit non-zero and must
    NOT proceed to establish any network connection."""
    assert_no_trading_credentials()
