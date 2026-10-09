from __future__ import annotations

from dataclasses import replace
import logging

from app.config import load_all
from app.core.math import wilder_atr
from app.core.models import TakerFlowState, SymbolKlines
from app.strategies.s1_liquidity_sweep import S1LiquiditySweep
from tests.integration.test_pipeline_e2e import build_realistic_s1_snapshot

CFG = load_all().strategy
LOGGER_NAME = "app.strategies.s1_liquidity_sweep"


def _with_bars(snapshot, bars):
    klines = dict(snapshot.klines)
    klines["5m"] = replace(klines["5m"], bars=bars)
    return replace(snapshot, klines=klines)


def test_s1_evaluate_rejects_zero_reclaim_quality(caplog):
    snapshot = build_realistic_s1_snapshot()
    bars = list(snapshot.klines["5m"].bars)
    atr14 = wilder_atr(bars, period=14)
    l_high = max(bar.high for bar in bars[-4:-1])
    current = bars[-1]
    reclaim_band_atr = CFG["s1_liquidity_sweep"]["reclaim_band_atr"]
    # A tiny positive reclaim is raw-valid but below the 0.5 quality
    # threshold after normalization, so it must fail at factor stage.
    bars[-1] = replace(current, close=l_high - 0.01 * atr14)
    snapshot = _with_bars(snapshot, bars)

    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)

    assert result == []
    assert "s1_candidate_rejected" in caplog.text
    assert "reason=zero_confidence" in caplog.text
    assert "reclaim_quality_factor" in caplog.text


def test_s1_evaluate_rejects_zero_volume_factor(caplog):
    snapshot = build_realistic_s1_snapshot()
    snapshot = replace(
        snapshot,
        taker_flow=replace(
            snapshot.taker_flow,
            # Raw taker-flow gating still passes: 24 / 25 = 0.96.
            # The trailing volume is below its mean, so volume_factor is 0.
            total_volume_last_bars=[10.0, 10.0, 5.0],
        ),
    )

    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    result = S1LiquiditySweep().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)

    assert result == []
    assert "s1_candidate_rejected" in caplog.text
    assert "reason=zero_confidence" in caplog.text
    assert "volume_factor" in caplog.text


def _empty_news(as_of):
    from app.core.models import NewsState

    return NewsState(as_of_ts_ms=as_of, active_events=(), unhealthy_categories=frozenset())
