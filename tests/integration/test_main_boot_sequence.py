from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.bot import SignalBot, SnapshotReadinessError
from app.config import load_and_validate_all
from app.core.errors import ConfigError, MissingAssumedEquityError
from app.core.models import FeedHealth
from app.core.time_utils import now_ms
from app.data.binance.models import RawAggTrade, RawExchangeInfoSymbol, RawFundingRate, RawKline, RawLongShortRatio, RawOpenInterest
from app.main import BootDependencies, BootStageError, UniverseValidationError, main, run_application, validate_exchange_universe


def _exchange_rows(cfg, invalid: dict[str, str] | None = None):
    invalid = invalid or {}
    rows = []
    for symbol in cfg.enabled_symbols():
        p = cfg.pair_config(symbol)
        rows.append(RawExchangeInfoSymbol(symbol, invalid.get(symbol, "TRADING"), p["price_tick"], p["qty_step"], p["min_qty"]))
    return rows


class FakeRest:
    def __init__(self, cfg):
        self.cfg = cfg
        self.closed = False

    async def exchange_info(self):
        return _exchange_rows(self.cfg)

    async def klines(self, symbol, interval, limit=500):
        width = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
        now = now_ms()
        return [RawKline(now-(limit-i)*width, 100.0, 101.0, 99.0, 100.0, 10.0,
                         now-(limit-i)*width+width-1, 5.0, True) for i in range(limit)]

    async def depth(self, symbol, limit=20):
        return {"lastUpdateId": 10, "bids": [["99.9", "100"] for _ in range(limit)],
                "asks": [["100.1", "100"] for _ in range(limit)]}

    async def book_ticker(self, symbol):
        return {"symbol": symbol, "bidPrice": "99.9", "bidQty": "100", "askPrice": "100.1", "askQty": "100"}

    async def agg_trades(self, symbol, limit=500):
        return [{"a": 1, "p": "100", "q": "1", "m": False, "T": now_ms()}]

    async def funding_rate(self, symbol, limit=100): return []

    async def open_interest_hist(self, symbol, period, limit=30):
        now = now_ms(); width = 86_400_000 if period == "1d" else 300_000
        return [RawOpenInterest(symbol, 1_000_000.0+i, now-(limit-i)*width) for i in range(limit)]

    async def global_long_short_account_ratio(self, symbol, period, limit=30): return []
    async def taker_long_short_ratio(self, symbol, period, limit=30): return []
    async def open_interest(self, symbol): return RawOpenInterest(symbol, 1_000_100.0, now_ms())
    async def close(self): self.closed = True


class FakeWebSocket:
    def __init__(self, config, streams):
        self.config = config
        self.streams = streams
        self._connected = False
        self.last = {}
        self.resync = None
        self.delivered = False

    @property
    def is_connected(self): return self._connected

    def set_resync_callback(self, callback): self.resync = callback

    async def messages(self):
        self._connected = True
        stream = next(s for s in self.streams if s.endswith("@bookTicker"))
        stamp = now_ms(); self.last[stream] = stamp; self.delivered = True
        yield stream, {"s": stream.split("@", 1)[0].upper(), "b": "99.9", "B": "100", "a": "100.1", "A": "100", "E": stamp}
        await asyncio.Event().wait()

    def feed_health(self, stream, as_of_ts_ms):
        return FeedHealth(stream.split("@", 1)[0].upper(), stream, self.last.get(stream, 0), 0, self._connected)


class _NoNetworkBot(SignalBot):
    async def start_error_notifier(self):
        self.notifier_start_skipped = True

    async def start_news_collectors(self):
        self.news_started = True

    async def wait_for_snapshot_readiness(self, symbols, timeout_s=90.0, min_ready_count=None):
        await super().wait_for_snapshot_readiness(symbols, timeout_s, min_ready_count)
        self.first_snapshot = self.cache.get_snapshot("BTCUSDT")

    async def run(self, stop_event=None):
        assert stop_event is not None
        stop_event.set()


class _RepoCloseOnly:
    async def close(self): pass


@pytest.mark.asyncio
async def test_full_boot_stages_in_order_with_mocked_exchange_and_one_ws_message():
    cfg = load_and_validate_all()
    rests, bots, stages = [], [], []

    def factory(config, equity):
        rest = FakeRest(config); rests.append(rest)
        bot = _NoNetworkBot(config, equity, rest_client=rest, ws_factory=FakeWebSocket,
                            repository=_RepoCloseOnly())
        bots.append(bot); return bot

    stop = asyncio.Event()
    await run_application(dependencies=BootDependencies(lambda _=None: cfg, lambda: 1000.0, factory, {}),
                          stage_callback=stages.append, stop_event=stop)
    assert stages == ["configuration_validation", "assumed_equity_validation", "trading_credential_guard",
                      "exchange_info_validation", "websocket_first_snapshot", "news_and_main_evaluation"]
    assert len(bots[0].symbols) == 20
    assert bots[0].first_snapshot.symbol == "BTCUSDT"
    assert bots[0].cache.ws.delivered is True
    assert rests[0].closed is True


@pytest.mark.asyncio
async def test_testnet_boot_warns_and_selects_testnet_websocket(capsys, caplog):
    cfg = load_and_validate_all()
    cfg.system["binance_env"] = "testnet"
    bots = []

    def factory(config, equity):
        bot = _NoNetworkBot(config, equity, rest_client=FakeRest(config), ws_factory=FakeWebSocket,
                            repository=_RepoCloseOnly())
        bots.append(bot)
        return bot

    stop = asyncio.Event()
    await run_application(dependencies=BootDependencies(lambda _=None: cfg, lambda: 1000.0, factory, {}),
                          stop_event=stop)
    warning = ("TESTNET MODE — market data is sparse and may not reflect real\n"
               "liquidity. This is for pipeline verification only, NOT for signal\n"
               "generation.")
    assert capsys.readouterr().err == warning + "\n"
    assert warning in caplog.text
    assert bots[0].cache.ws.config.base_ws_url == "wss://stream.binancefuture.com/stream"


def test_exchange_info_accepts_15_and_reports_each_rejected_symbol():
    cfg = load_and_validate_all(); invalid = {s: "BREAK" for s in cfg.enabled_symbols()[:5]}
    accepted, rejected = validate_exchange_universe(cfg, _exchange_rows(cfg, invalid))
    assert len(accepted) == 15
    assert rejected == [f"{s}: status is 'BREAK', not 'TRADING'" for s in cfg.enabled_symbols()[:5]]


def test_exchange_info_under_15_aborts_with_exact_rejection_list():
    cfg = load_and_validate_all(); invalid = {s: "HALT" for s in cfg.enabled_symbols()[:6]}
    with pytest.raises(UniverseValidationError) as caught:
        validate_exchange_universe(cfg, _exchange_rows(cfg, invalid))
    assert caught.value.stage == "exchange_info_validation"
    assert caught.value.rejected_symbols == [f"{s}: status is 'HALT', not 'TRADING'" for s in cfg.enabled_symbols()[:6]]
    assert all(item in str(caught.value) for item in caught.value.rejected_symbols)


def test_exchange_info_all_20_invalid_aborts():
    cfg = load_and_validate_all(); invalid = {s: "HALT" for s in cfg.enabled_symbols()}
    with pytest.raises(UniverseValidationError) as caught:
        validate_exchange_universe(cfg, _exchange_rows(cfg, invalid))
    assert len(caught.value.rejected_symbols) == 20
    assert "need at least 15" in str(caught.value)


def test_exchange_info_malformed_filters_are_reported_by_symbol():
    cfg=load_and_validate_all(); symbol=cfg.enabled_symbols()[0]
    rows=_exchange_rows(cfg)
    rows[0]={"symbol":symbol,"invalid_reason":"malformed exchange filters: DataIntegrityError: missing LOT_SIZE"}
    accepted,rejected=validate_exchange_universe(cfg,rows)
    assert len(accepted)==19
    assert rejected==[f"{symbol}: malformed exchange filters: DataIntegrityError: missing LOT_SIZE"]


@pytest.mark.asyncio
async def test_first_snapshot_gate_accepts_15_ready_of_20():
    cfg=load_and_validate_all()
    class ReadinessCache:
        def __init__(self):
            self.ws=type("WS",(),{"is_connected":True})()
            self.ws_task=asyncio.create_task(asyncio.Event().wait())
        async def backfill(self,symbols): pass
        def not_ready(self,symbols): return list(symbols[15:])
    bot=SignalBot(cfg,1000.0,rest_client=FakeRest(cfg),repository=_RepoCloseOnly())
    bot.cache=ReadinessCache()
    symbols=cfg.enabled_symbols()
    try:
        await bot.wait_for_snapshot_readiness(symbols,timeout_s=1.0)
    finally:
        bot.cache.ws_task.cancel()
        await asyncio.gather(bot.cache.ws_task,return_exceptions=True)


@pytest.mark.parametrize("bad_equity", [None, "not-a-number", "0", "-1", "nan", "inf"])
def test_missing_or_invalid_equity_stops_at_equity_stage_and_cli_propagates_real_error(bad_equity, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_and_validate_all(); visited = []
    def equity():
        if bad_equity is None or bad_equity == "not-a-number":
            raise MissingAssumedEquityError("invalid equity")
        return float(bad_equity)
    deps = BootDependencies(lambda _=None: cfg, equity, lambda *_: pytest.fail("bot must not be created"), {})
    with pytest.raises(MissingAssumedEquityError) as caught:
        asyncio.run(run_application(dependencies=deps, stage_callback=visited.append))
    assert caught.value.boot_stage == "assumed_equity_validation"
    assert visited == ["configuration_validation", "assumed_equity_validation"]
    with pytest.raises(MissingAssumedEquityError):
        main(dependencies=deps)
    assert (tmp_path / "logs").is_dir()


def test_bad_config_stops_before_equity_and_bot_creation():
    visited = []
    deps = BootDependencies(lambda _=None: (_ for _ in ()).throw(ConfigError("broken config")),
                            lambda: pytest.fail("equity must not be read"),
                            lambda *_: pytest.fail("bot must not be created"), {})
    with pytest.raises(ConfigError) as caught:
        asyncio.run(run_application(dependencies=deps, stage_callback=visited.append))
    assert caught.value.boot_stage == "configuration_validation"
    assert visited == ["configuration_validation"]


@pytest.mark.parametrize("credential", ["BINANCE_API_KEY", "BINANCE_API_SECRET"])
def test_present_trading_credential_aborts_before_exchange_info(credential):
    cfg = load_and_validate_all(); visited=[]; made=[]
    deps = BootDependencies(lambda _=None: cfg, lambda: 1000.0,
                            lambda *_: made.append(True), {credential: "test-only-not-a-secret"})
    with pytest.raises(BootStageError) as caught:
        asyncio.run(run_application(dependencies=deps, stage_callback=visited.append))
    assert caught.value.boot_stage == "trading_credential_guard"
    assert credential in str(caught.value)
    assert made == []
    assert visited[-1] == "trading_credential_guard"


@pytest.mark.asyncio
async def test_snapshot_timeout_reports_exact_symbols_and_stage():
    cfg = load_and_validate_all(); visited=[]

    class TimeoutBot:
        async def exchange_info(self): return _exchange_rows(cfg)
        async def start_market_data(self, symbols): self.symbols=symbols
        async def wait_for_snapshot_readiness(self, symbols, timeout_s, min_ready_count): raise SnapshotReadinessError(["BTCUSDT", "ETHUSDT"])
        async def shutdown(self): self.closed=True

    bot=TimeoutBot(); deps=BootDependencies(lambda _=None:cfg,lambda:1000.0,lambda *_:bot,{})
    with pytest.raises(BootStageError) as caught:
        await run_application(dependencies=deps,stage_callback=visited.append)
    assert caught.value.boot_stage=="websocket_first_snapshot"
    assert "['BTCUSDT', 'ETHUSDT']" in str(caught.value)
    assert bot.closed is True


@pytest.mark.asyncio
async def test_signal_bot_exchange_info_parses_public_payload_without_network():
    cfg = load_and_validate_all()
    payload = {
        "symbols": [{
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            ],
        }]
    }

    class PayloadRest:
        async def _get(self, path):
            assert path == "/fapi/v1/exchangeInfo"
            return payload

    bot = SignalBot(cfg, 2400.0, rest_client=PayloadRest(), repository=_RepoCloseOnly())
    assert await bot.exchange_info() == [RawExchangeInfoSymbol("BTCUSDT", "TRADING", 0.1, 0.001, 0.001)]
