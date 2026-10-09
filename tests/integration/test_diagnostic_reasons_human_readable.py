from __future__ import annotations

import logging
import re

from app.monitoring import diagnostics


def test_all_strategy_diagnostic_reasons_are_snake_case(caplog):
    diagnostics.configure("verbose")
    with caplog.at_level(logging.INFO, logger="app.monitoring.diagnostics"):
        for strategy_name, reason in {
            "S1": "if len(bars_5m) < min_candles:",
            "S2": "if not compression:",
            "S3": "):",
            "S4": "if len(bars_1h) < required_1h:",
            "S5": "if abs(oi_delta_5m) < noise_band:",
        }.items():
            diagnostics.strategy("BTCUSDT", strategy_name, "rejected", reason)
    records = [r for r in caplog.records if r.name == "app.monitoring.diagnostics"]
    assert len(records) == 5
    for record in records:
        reason = record.getMessage().split('"reason": "', 1)[1].split('"', 1)[0]
        assert re.fullmatch(r"[a-z][a-z0-9_]*", reason)
        assert ":" not in reason and "if" not in reason and "(" not in reason
