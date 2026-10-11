from app.signals.models import Signal
from app.telegram.formatter import DeliveryContext, format_signal_message


def _model_b_signal() -> Signal:
    return Signal(
        signal_id="CSB-20261011-MODELB",
        created_ts_ms=1_000_000,
        symbol="BTCUSDT",
        direction="LONG",
        grade="B",
        confidence=0.68,
        strategy_source="S3",
        entry_low=100.0,
        entry_high=100.5,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        tp3=103.0,
        tp4=105.0,
        rr_tp2=2.0,
        expiry_ts_ms=1_000_000 + 120 * 60_000,
        entry_expiry_ts_ms=1_000_000 + 30 * 60_000,
        management_horizon_minutes=120,
        time_stop_minutes=60,
        partial_exit_fractions=(0.4, 0.3, 0.2, 0.1),
        why_lines=["test"],
        veto_state="PASS",
        size_units_advisory=1.0,
        notional_usd_advisory=100.0,
        meta={"model": "B"},
    )


def test_model_b_keeps_entry_and_management_horizons_separate():
    signal = _model_b_signal()
    assert signal.entry_expiry_ts_ms == signal.created_ts_ms + 30 * 60_000
    assert signal.expiry_ts_ms == signal.created_ts_ms + 120 * 60_000
    assert signal.management_horizon_minutes == 120
    assert signal.time_stop_minutes == 60
    assert signal.partial_exit_fractions == (0.4, 0.3, 0.2, 0.1)


def test_telegram_message_shows_strategy_and_model_b_rules():
    message = format_signal_message(
        _model_b_signal(),
        DeliveryContext(news_state_label="none", binance_state_label="connected", as_of_ts_ms=1_000_000),
    )
    assert "S3" in message
    assert "Funding Crowding" in message
    assert "Entry" in message
    assert "30m" in message
    assert "120m" in message
    assert "60m" in message
    assert "40/30/20/10%" in message
