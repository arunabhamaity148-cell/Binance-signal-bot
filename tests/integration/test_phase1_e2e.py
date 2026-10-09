from app.risk.regime_detector import MarketRegime, classify_regime
from app.risk.risk_engine import vol_adjusted_position_size
from app.signals.models import Signal
from app.telegram.formatter import DeliveryContext, format_signal_message


def _signal(*, regime, sizing_multiplier, htf_confluence):
    return Signal(
        signal_id="CSB-20261009-PHASE1", created_ts_ms=1, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=.8, strategy_source="S4", entry_low=100, entry_high=100.5,
        stop_loss=99, tp1=101, tp2=102, tp3=103, tp4=104, rr_tp2=1.5, expiry_ts_ms=10_000,
        why_lines=["synthetic Phase 1 pipeline"], veto_state="PASS", veto_reason=None,
        size_units_advisory=45, notional_usd_advisory=4500, sizing_multiplier=sizing_multiplier,
        regime=regime.value, htf_confluence=htf_confluence, meta={},
    )


def test_phase1_trending_confluence_and_high_vol_sizing():
    trending = classify_regime(adx14=28, atr_percentile=.45, oi_change_1h_pct=2.0)
    assert trending == MarketRegime.TRENDING
    assert vol_adjusted_position_size(100, "HIGH_VOLATILITY", .5) == 45
    signal = _signal(regime=MarketRegime.HIGH_VOLATILITY, sizing_multiplier=.45, htf_confluence=True)
    message = format_signal_message(signal, DeliveryContext("clear", "connected", 1))
    assert "Size adjusted: 0.5x" in message or "Size adjusted: 0.4x" in message
    assert "1H/4H confluence confirmed" in message
