from __future__ import annotations
from app.backtest.fills import (FillAssessment, deterministic_fill_uniform,
    fill_probability_succeeds)

def test_fill_decision_is_deterministic_from_signal_id_and_bar_timestamp():
    assessment=FillAssessment(is_filled=True,fill_probability=0.37,fill_price=100.0)
    got=fill_probability_succeeds(assessment,signal_id="S2:BTCUSDT:LONG:123",bar_timestamp_ms=456)
    again=fill_probability_succeeds(assessment,signal_id="S2:BTCUSDT:LONG:123",bar_timestamp_ms=456)
    expected=deterministic_fill_uniform("S2:BTCUSDT:LONG:123",456)<0.37
    assert got==again==expected
    assert deterministic_fill_uniform("S2:BTCUSDT:LONG:123",456)!=deterministic_fill_uniform("S2:BTCUSDT:LONG:457",456)

def test_fill_probability_zero_and_one_are_respected():
    assert not fill_probability_succeeds(FillAssessment(True,0.0,100),signal_id="id",bar_timestamp_ms=1)
    assert fill_probability_succeeds(FillAssessment(True,1.0,100),signal_id="id",bar_timestamp_ms=1)
    assert not fill_probability_succeeds(FillAssessment(False,1.0,None),signal_id="id",bar_timestamp_ms=1)
