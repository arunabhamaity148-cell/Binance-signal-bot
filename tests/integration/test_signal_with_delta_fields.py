from __future__ import annotations

from app.signals.models import Signal
from app.telegram.formatter import MAX_MESSAGE_CHARS, DeliveryContext, format_signal_message


def _signal(**overrides):
    values = dict(
        signal_id="CSB-20261009-DELTA1", created_ts_ms=0, symbol="BTCUSDT", direction="LONG",
        grade="A", confidence=0.84, strategy_source="S1",
        entry_low=62150.0, entry_high=62200.0, stop_loss=61920.0,
        tp1=62400.0, tp2=62650.0, tp3=63000.0, tp4=63400.0, rr_tp2=2.1,
        expiry_ts_ms=45 * 60_000, why_lines=["synthetic fixture"], veto_state="PASS", veto_reason=None,
        size_units_advisory=0.05, notional_usd_advisory=3108.75, meta={},
        delta_available=True, delta_symbol="BTCUSDT", delta_contracts=50.0,
        delta_entry_low=62150.5, delta_entry_high=62200.0, delta_stop_loss=61920.0,
        delta_tp1=62400.0, delta_tp2=62650.5, delta_tp3=63000.0, delta_tp4=63400.0,
        delta_rr_tp2=1.92,
    )
    values.update(overrides)
    return Signal(**values)


def _ctx(news="no blocking event", binance="healthy"):
    return DeliveryContext(news_state_label=news, binance_state_label=binance, as_of_ts_ms=0)


def test_formatter_renders_full_delta_execution_block():
    message = format_signal_message(_signal(), _ctx())
    assert "━━━ DELTA EXECUTION ━━━" in message
    assert "📦 Contracts: `50.00`" in message
    assert "`62150.50`" in message and "`62200.00`" in message
    assert "📐 Delta R:R: `1:1.9`" in message
    assert "reduce_only" in message
    assert len(message) <= MAX_MESSAGE_CHARS


def test_truncation_preserves_delta_block_and_id():
    message = format_signal_message(_signal(), _ctx(news="N" * 2000))
    assert len(message) <= MAX_MESSAGE_CHARS
    assert "━━━ DELTA EXECUTION ━━━" in message
    assert "ID: CSB-20261009-DELTA1" in message


def test_binance_only_signal_renders_fallback_notice():
    message = format_signal_message(_signal(delta_available=False), _ctx())
    assert "⚠️ Delta prices unavailable — using Binance values only." in message
    assert "━━━ DELTA EXECUTION ━━━" not in message
