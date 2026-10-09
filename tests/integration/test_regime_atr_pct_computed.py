from dataclasses import replace

from app.core.math import OHLC
from app.risk.regime_detector import MarketRegime, detect_regime
from tests.integration.test_pipeline_e2e import build_realistic_s1_snapshot


def test_500_five_minute_klines_compute_atr_percentile_and_regime():
    snapshot = build_realistic_s1_snapshot()
    bars = list(snapshot.klines["5m"].bars)
    last = bars[-1].close_time_ms
    for i in range(203):
        close = 100_000.0 + i * 10
        bars.append(OHLC(close, close + 120, close - 120, close, 100.0, last + (i + 1) * 300_000))
    klines = dict(snapshot.klines)
    klines["5m"] = replace(klines["5m"], bars=bars)
    snapshot = replace(snapshot, klines=klines)

    regime, metrics = detect_regime(snapshot)

    assert len(bars) == 500
    assert metrics.atr_percentile is not None
    assert 0.0 <= metrics.atr_percentile <= 1.0
    assert regime in {MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.HIGH_VOLATILITY}
