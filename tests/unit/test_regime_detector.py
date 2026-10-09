from app.risk.regime_detector import MarketRegime, classify_regime


def test_synthetic_trending_metrics_classify_trending():
    assert classify_regime(adx14=28, atr_percentile=.45, oi_change_1h_pct=1.2) == MarketRegime.TRENDING


def test_synthetic_ranging_metrics_classify_ranging():
    assert classify_regime(adx14=18, atr_percentile=.45, oi_change_1h_pct=.4) == MarketRegime.RANGING


def test_synthetic_high_volatility_metrics_classify_high_volatility():
    assert classify_regime(adx14=18, atr_percentile=.95, oi_change_1h_pct=0.5) == MarketRegime.HIGH_VOLATILITY
    assert classify_regime(adx14=18, atr_percentile=.45, oi_change_1h_pct=5.1) == MarketRegime.HIGH_VOLATILITY


def test_missing_metrics_are_unknown_and_not_strategy_eligible():
    assert classify_regime(adx14=None, atr_percentile=.45, oi_change_1h_pct=1.2) == MarketRegime.UNKNOWN
    assert classify_regime(adx14=28, atr_percentile=None, oi_change_1h_pct=1.2) == MarketRegime.UNKNOWN
    assert classify_regime(adx14=28, atr_percentile=.45, oi_change_1h_pct=None) == MarketRegime.UNKNOWN
