from app.risk.regime_detector import MarketRegime, classify_regime


def test_strong_adx_alone_is_trending():
    assert classify_regime(adx14=45, atr_percentile=.20, oi_change_1h_pct=.05) == MarketRegime.TRENDING
    assert classify_regime(adx14=40, atr_percentile=.20, oi_change_1h_pct=0.0) == MarketRegime.TRENDING


def test_moderate_adx_requires_only_moderate_oi_confirmation():
    assert classify_regime(adx14=30, atr_percentile=.20, oi_change_1h_pct=0.0) == MarketRegime.RANGING
    assert classify_regime(adx14=30, atr_percentile=.20, oi_change_1h_pct=.8) == MarketRegime.TRENDING


def test_weak_adx_low_oi_is_ranging():
    assert classify_regime(adx14=15, atr_percentile=.20, oi_change_1h_pct=.2) == MarketRegime.RANGING


def test_negative_large_oi_change_is_high_volatility():
    assert classify_regime(adx14=15, atr_percentile=.20, oi_change_1h_pct=-5.1) == MarketRegime.HIGH_VOLATILITY
