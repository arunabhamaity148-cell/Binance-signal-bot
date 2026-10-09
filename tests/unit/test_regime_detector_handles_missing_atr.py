from dataclasses import replace

from app.risk.regime_detector import MarketRegime, detect_regime
from tests.integration.test_pipeline_e2e import build_realistic_s1_snapshot


def test_missing_atr_percentile_uses_safe_ranging_default_and_warns(caplog):
    snapshot = build_realistic_s1_snapshot()
    klines = dict(snapshot.klines)
    klines["5m"] = replace(klines["5m"], bars=klines["5m"].bars[:100])
    snapshot = replace(snapshot, symbol="MISSINGATR", klines=klines)

    with caplog.at_level("WARNING", logger="app.risk.regime_detector"):
        regime, metrics = detect_regime(snapshot)

    assert metrics.atr_percentile is None
    assert regime == MarketRegime.RANGING
    assert any("regime_atr_pct_unavailable" in record.getMessage() for record in caplog.records)
