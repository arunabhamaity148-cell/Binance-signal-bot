"""Strategy registry.

Central place that knows about every implemented strategy. The main
evaluation loop and the backtest engine both iterate
`all_strategies()` rather than importing S1..S5 individually, so
adding or removing a strategy is a one-line change here.
"""

from __future__ import annotations

from app.strategies.base import StrategyBase
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep
from app.strategies.s2_volatility_compression import S2VolatilityCompression
from app.strategies.s3_funding_crowding import S3FundingCrowding
from app.strategies.s4_oi_trend import S4OiTrend
from app.strategies.s5_oi_regime import S5OiRegime

_REGISTRY: dict[str, StrategyBase] = {
    "S1": S1LiquiditySweep(),
    "S2": S2VolatilityCompression(),
    "S3": S3FundingCrowding(),
    "S4": S4OiTrend(),
    "S5": S5OiRegime(),
}


def all_strategies() -> list[StrategyBase]:
    return list(_REGISTRY.values())


def get_strategy(strategy_id: str) -> StrategyBase:
    if strategy_id not in _REGISTRY:
        raise KeyError(f"unknown strategy_id: {strategy_id!r}")
    return _REGISTRY[strategy_id]


def registered_strategy_ids() -> list[str]:
    return list(_REGISTRY.keys())
