from __future__ import annotations

import logging

from app.config import load_all
from app.monitoring import diagnostics
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep
from tests.strategies.test_s1_liquidity_sweep import _base_snapshot, _empty_news, _flat_bars


def test_s1_diagnostics_are_emitted_for_early_return(caplog):
    diagnostics.configure("summary")
    snapshot = _base_snapshot(_flat_bars(10, 100.0), None, 3_000_000)
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        assert S1LiquiditySweep().evaluate(snapshot, _empty_news(), load_all().strategy) == []
    records = [r for r in caplog.records if r.name == "app.monitoring.diagnostics"]
    assert records
    assert '"strategy": "S1"' in records[-1].getMessage()
    assert '"reason": "insufficient_history"' in records[-1].getMessage()
