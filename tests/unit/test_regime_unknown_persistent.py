import logging

from app.bot import SignalBot
from app.risk.regime_detector import MarketRegime


def test_unknown_regime_logs_error_after_ten_minutes(monkeypatch, caplog):
    bot = object.__new__(SignalBot)
    bot._unknown_regime_since = {}
    bot._unknown_regime_error_logged = set()
    clock = {"value": 100.0}
    monkeypatch.setattr("app.bot.time.monotonic", lambda: clock["value"])

    assert bot._track_regime_status("BTCUSDT", MarketRegime.UNKNOWN) == 0.0
    clock["value"] = 701.0
    with caplog.at_level(logging.ERROR, logger="app.bot"):
        duration = bot._track_regime_status("BTCUSDT", MarketRegime.UNKNOWN)

    assert duration == 601.0
    assert any("regime_unknown_persistent" in record.getMessage() for record in caplog.records)
