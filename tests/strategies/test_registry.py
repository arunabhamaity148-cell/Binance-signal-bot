"""Registry-level regression tests: every strategy is registered and
every strategy routes its output through StrategyBase.finalize_candidates
(the shared min-stop-cost and entry-zone-width filters), so neither
filter can be silently skipped by an individual strategy implementation.
"""
from __future__ import annotations

import inspect

import pytest

from app.strategies.base import StrategyBase
from app.strategies.registry import all_strategies, get_strategy, registered_strategy_ids


def test_all_five_strategies_registered():
    assert registered_strategy_ids() == ["S1", "S2", "S3", "S4", "S5"]
    assert len(all_strategies()) == 5


def test_each_strategy_id_matches_its_registry_key():
    for sid in registered_strategy_ids():
        assert get_strategy(sid).strategy_id == sid


def test_each_strategy_is_a_strategybase_instance():
    for strat in all_strategies():
        assert isinstance(strat, StrategyBase)


@pytest.mark.parametrize("strategy_id", ["S1", "S2", "S3", "S4", "S5"])
def test_strategy_routes_through_finalize_candidates(strategy_id):
    """Every registered strategy's evaluate() must call
    self.finalize_candidates(...) on its way out, so the shared
    min_stop_cost_multiple and max_entry_zone_width_r_multiple filters
    are applied uniformly and cannot be forgotten per-strategy."""
    strat = get_strategy(strategy_id)
    src = inspect.getsource(type(strat))
    assert "finalize_candidates" in src, (
        f"{strategy_id} ({type(strat).__name__}) does not call finalize_candidates"
    )


def test_get_strategy_unknown_id_raises():
    with pytest.raises(KeyError):
        get_strategy("S9")
