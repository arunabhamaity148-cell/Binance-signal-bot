from __future__ import annotations

from types import SimpleNamespace

from app.core.math import OHLC
from app.core.models import CandidateSignal, Direction
from app.risk.veto_g16 import guard_g16_multi_tf_confluence
from tests.integration.test_g16_4h_structure_detection import _structure_bars


CFG = {
    "htf_1h_ema_fast": 21,
    "htf_1h_ema_slow": 55,
    "htf_4h_structure_bars": 40,
}


def _candidate() -> CandidateSignal:
    return CandidateSignal(
        symbol="BTCUSDT", direction=Direction.SHORT, strategy_source="S1", confidence=0.8,
        channels=(), entry_low=100.0, entry_high=101.0, stop_loss=102.0,
        tp1=99.0, tp2=98.0, tp3=97.0, tp4=96.0, why_lines=(), meta={}, event_ts_ms=1,
    )


def _downtrend_1h_bars() -> list[OHLC]:
    return [
        OHLC(200.0 - i, 201.0 - i, 199.0 - i, 200.0 - i, 100.0, i)
        for i in range(60)
    ]


def test_short_candidate_in_downtrend_with_lh_ll_structure_passes_g16():
    bars_1h = _downtrend_1h_bars()
    bars_4h = _structure_bars("down")
    snapshot = SimpleNamespace(
        symbol="BTCUSDT",
        klines_for=lambda timeframe: bars_1h if timeframe == "1h" else bars_4h,
    )

    result = guard_g16_multi_tf_confluence(snapshot, None, _candidate(), CFG)

    assert result.passed
    assert result.guard_name == "G16"
