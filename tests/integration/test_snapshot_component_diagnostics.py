from __future__ import annotations

import logging

from app.bot import LiveSnapshotCache, SignalBot
from app.config import load_and_validate_all


def test_incomplete_snapshot_logs_component_labels_symbol_and_elapsed_boot_time(caplog):
    cfg = load_and_validate_all()
    bot = SignalBot(
        cfg,
        2400.0,
        rest_client=object(),
        repository=object(),
    )
    bot.cache = LiveSnapshotCache(cfg, rest=object())
    bot.cache.data["BTCUSDT"] = {
        "klines": {},
        "depth": None,
        "ticker": None,
        "trades": [],
        "derivatives": None,
        "oi": None,
        "error": None,
    }

    with caplog.at_level(logging.WARNING, logger="app.bot"):
        bot._log_snapshot_incomplete("BTCUSDT")

    record = next(record for record in caplog.records if record.name == "app.bot")
    assert record.context["symbol"] == "BTCUSDT"
    assert record.context["elapsed_boot_s"] >= 0
    assert record.context["missing_components"] == [
        "missing=kline",
        "missing=orderbook",
        "missing=derivatives",
        "missing=taker_flow",
        "missing=oi",
    ]
