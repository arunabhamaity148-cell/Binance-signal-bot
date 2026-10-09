from types import SimpleNamespace

from app.core.math import OHLC
from app.core.models import CandidateSignal, Direction
from app.risk.veto_g16 import guard_g16_multi_tf_confluence

CFG = {"htf_1h_ema_fast": 21, "htf_1h_ema_slow": 55, "htf_4h_structure_bars": 20}


def _candidate(direction):
    return CandidateSignal(
        symbol="BTCUSDT", direction=direction, strategy_source="S4", confidence=.8,
        channels=(), entry_low=100, entry_high=100, stop_loss=99,
        tp1=101, tp2=102, tp3=103, tp4=104, why_lines=(), meta={}, event_ts_ms=1,
    )


def _bars(direction, timeframe):
    count = 60 if timeframe == "1h" else 20
    bars = []
    for i in range(count):
        base = 100 + (i if direction == Direction.LONG else -i)
        high = base + 1
        low = base - 1
        if timeframe == "4h":
            if i in (3, 11):
                high = base + (6 if i == 11 else 4)
            if i in (7, 15):
                low = base - (4 if i == 7 else 4)
            if direction == Direction.SHORT:
                if i in (3, 11):
                    high = base + 5
                if i in (7, 15):
                    low = base - (5 if i == 7 else 6)
        bars.append(OHLC(base, high, low, base, 100, i + 1))
    return bars


def _snapshot(direction=Direction.LONG, missing_1h=False):
    data = {"1h": [] if missing_1h else _bars(direction, "1h"), "4h": _bars(direction, "4h")}
    return SimpleNamespace(symbol="BTCUSDT", klines_for=lambda tf: data[tf])


def test_aligned_1h_4h_long_passes():
    result = guard_g16_multi_tf_confluence(_snapshot(), None, _candidate(Direction.LONG), CFG)
    assert result.passed


def test_aligned_1h_4h_short_passes():
    result = guard_g16_multi_tf_confluence(_snapshot(Direction.SHORT), None, _candidate(Direction.SHORT), CFG)
    assert result.passed


def test_1h_up_4h_down_blocks_long():
    snapshot = SimpleNamespace(
        symbol="BTCUSDT",
        klines_for=lambda tf: _bars(Direction.LONG, "1h") if tf == "1h" else _bars(Direction.SHORT, "4h"),
    )
    result = guard_g16_multi_tf_confluence(snapshot, None, _candidate(Direction.LONG), CFG)
    assert not result.passed and result.guard_name == "G16"


def test_missing_1h_blocks_fail_closed():
    result = guard_g16_multi_tf_confluence(_snapshot(missing_1h=True), None, _candidate(Direction.LONG), CFG)
    assert not result.passed
    assert "missing" in (result.reason or "")
