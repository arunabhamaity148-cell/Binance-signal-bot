"""Canonical channel taxonomy (spec section 11).

Maps each strategy's underlying information sources to one of six
canonical channels, so that correlated evidence (e.g. wick size,
volume, and taker-flow ratio all describing the same aggressive-order
event) is not double-counted as independent confirmation in the
effective-vote consensus calculation (risk/consensus.py).

This module contains only the static taxonomy; the weighting math
itself lives in consensus.py.
"""

from __future__ import annotations

from app.core.models import ChannelName

# Per STRATEGIES_SPEC.md "double-counting avoidance" notes for each
# strategy, and spec section 11's canonical channel definitions.
STRATEGY_CHANNELS: dict[str, tuple[ChannelName, ...]] = {
    "S1": (ChannelName.LIQUIDITY, ChannelName.TAKER_FLOW),
    "S2": (ChannelName.VOLATILITY, ChannelName.OI),
    "S3": (ChannelName.FUNDING, ChannelName.OI),
    "S4": (ChannelName.PRICE_STRUCTURE, ChannelName.OI),
    "S5": (ChannelName.OI, ChannelName.TAKER_FLOW),
}


def channels_for_strategy(strategy_id: str) -> tuple[ChannelName, ...]:
    if strategy_id not in STRATEGY_CHANNELS:
        raise KeyError(f"unknown strategy_id: {strategy_id!r}")
    return STRATEGY_CHANNELS[strategy_id]
