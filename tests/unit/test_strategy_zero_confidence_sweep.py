from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_DIR = ROOT / "app" / "strategies"


def test_s1_zero_confidence_guard_precedes_both_candidate_constructors():
    source = (STRATEGY_DIR / "s1_liquidity_sweep.py").read_text()
    guard = 'if breakdown["final"] <= 0.0:'

    assert source.count(guard) == 2
    first_guard = source.index(guard)
    second_guard = source.index(guard, first_guard + 1)
    first_constructor = source.index("return CandidateSignal(", first_guard)
    second_constructor = source.index("return CandidateSignal(", second_guard)
    assert first_guard < first_constructor
    assert second_guard < second_constructor


def test_other_strategies_have_strictly_positive_confidence_floors():
    floors = {
        "s2_volatility_compression.py": "0.5 + 0.1 * volume_confirm_ratio",
        "s3_funding_crowding.py": "0.4 + 0.1 * abs(funding_z)",
        "s4_oi_trend.py": "0.5 + abs(ema_slope_fast) * 0.1",
        "s5_oi_regime.py": "0.5 + abs(oi_delta_5m) * 5",
    }

    for filename, floor_expression in floors.items():
        source = (STRATEGY_DIR / filename).read_text()
        assert floor_expression in source, filename
        assert "confidence=min(1.0" in source or "confidence = min(1.0" in source, filename
