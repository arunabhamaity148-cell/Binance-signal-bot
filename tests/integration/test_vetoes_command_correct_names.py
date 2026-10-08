from __future__ import annotations

import asyncio

from app.config import load_and_validate_all
from app.telegram.commands import CommandContext, dispatch_command


def test_vetoes_command_uses_actual_guard_names_and_explanations():
    context = CommandContext(
        repository=object(), config=load_and_validate_all(), allowed_chat_ids=frozenset({"123"})
    )
    message = asyncio.run(dispatch_command("/vetoes", chat_id="123", context=context))
    assert message is not None
    expected = (
        "Data Integrity", "Feed Health", "Depth Collapse", "Spread Explosion", "OI Anomaly",
        "Funding Extreme", "News Shock", "Volatility Flash", "BTC Regime", "Orderbook Instability",
        "Execution Quality", "Self-Consistency", "OI Divergence", "OI Stagnation", "OI Percentile Extreme",
    )
    for name in expected:
        assert name in message
    assert "BTC Regime" in message and "Correlation" not in message
    assert "Self-Consistency" in message and "Session" not in message
    assert "OI Percentile Extreme" in message and "Final Safety" not in message
    assert "🛡️ VETO GUARD GUIDE" in message
    assert "ID: CMD-VETOES" in message
    assert len(message) <= 1024
