from __future__ import annotations

import pytest

from app.config import load_and_validate_all
from app.data.binance.models import RawExchangeInfoSymbol
from app.main import BootDependencies, main


def test_main_creates_logs_directory_before_boot_logging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs_dir = tmp_path / "logs"
    assert not logs_dir.exists()
    cfg = load_and_validate_all()
    symbols = cfg.enabled_symbols()
    exchange_rows = [
        RawExchangeInfoSymbol(
            symbol,
            "TRADING",
            cfg.pair_config(symbol)["price_tick"],
            cfg.pair_config(symbol)["qty_step"],
            cfg.pair_config(symbol)["min_qty"],
        )
        for symbol in symbols
    ]

    class BootBot:
        async def start_error_notifier(self):
            pass

        async def exchange_info(self):
            return exchange_rows

        async def start_market_data(self, accepted_symbols):
            assert accepted_symbols == symbols

        async def wait_for_snapshot_readiness(self, accepted_symbols, timeout_s, min_ready_count):
            assert accepted_symbols == symbols
            assert min_ready_count == 15

        async def start_news_collectors(self):
            pass

        async def run(self, stop_event=None):
            assert stop_event is not None
            stop_event.set()

        async def shutdown(self):
            self.closed = True

    bots = []

    def load_config(_config_dir=None):
        # This loader runs after main() has created logs/ and before boot logs.
        assert logs_dir.is_dir()
        return cfg

    def make_bot(config, equity):
        bot = BootBot()
        bots.append(bot)
        return bot

    dependencies = BootDependencies(
        config_loader=load_config,
        equity_loader=lambda: 2400.0,
        bot_factory=make_bot,
        environ={},
    )

    assert main(dependencies=dependencies) == 0
    assert logs_dir.is_dir()
    assert len(bots) == 1
    assert bots[0].closed is True
