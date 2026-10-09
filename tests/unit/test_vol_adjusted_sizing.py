import pytest

from app.risk.risk_engine import combined_sizing_multiplier, vol_adjusted_position_size


@pytest.mark.parametrize(
    ("base", "regime", "atr_pct", "expected"),
    [
        (100, "TRENDING", .2, 100),
        (100, "HIGH_VOLATILITY", .5, 45),
        (100, "RANGING", .95, 40),
    ],
)
def test_phase1_sizing_examples(base, regime, atr_pct, expected):
    assert vol_adjusted_position_size(base, regime, atr_pct) == pytest.approx(expected)


def test_combined_multiplier_is_bounded():
    assert .25 <= combined_sizing_multiplier("HIGH_VOLATILITY", .99) <= 1.0
    assert combined_sizing_multiplier("TRENDING", .2) == 1.0
