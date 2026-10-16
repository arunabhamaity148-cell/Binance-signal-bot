import pytest
from app.config import load_all
from app.bot import SignalBot
from app.core.models import MarketSnapshot
from types import SimpleNamespace
import logging
from dataclasses import replace

class _Closable:
    async def close(self): pass

class _News:
    def build_news_state(self, _stamp):
        return SimpleNamespace(active_for=lambda _symbol: ())

class _WS:
    is_connected = True

class _Cache:
    def __init__(self):
        self.ws = _WS()
        self.data = {"BTCUSDT": {"error": None}}
    def get_snapshot(self, symbol):
        return SimpleNamespace(symbol=symbol, snapshot_version="test", klines_for=lambda tf: [], derivatives=None)
    def is_snapshot_ready(self, _symbol): return True
    def snapshot_missing_components(self, _symbol): return []

@pytest.mark.asyncio
async def test_funnel_telemetry_live_path_integration(monkeypatch, caplog):
    cfg = load_all()
    bot = SignalBot(cfg, 2400.0, rest_client=_Closable(), repository=_Closable())
    bot.symbols = ["BTCUSDT"]
    bot.news_engine = _News()
    bot.cache = _Cache()

    import app.bot as bot_module
    from app.risk.regime_detector import MarketRegime, RegimeMetrics

    # 1. Test UNKNOWN regime
    monkeypatch.setattr(bot_module, "detect_regime", lambda snap, fz: (MarketRegime.UNKNOWN, RegimeMetrics(None, None, None, None)))
    import app.monitoring.diagnostics as diagnostics
    diagnostics.configure("summary")

    with caplog.at_level(logging.INFO):
        from app.core.models import MarketSnapshot
        await bot.evaluate_symbol(MarketSnapshot(symbol="BTCUSDT", snapshot_version="test-v1", as_of_ts_ms=1, klines={}, orderbook=None, taker_flow=None, derivatives=None, feed_health={}, price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=0.02, fee_taker_bps=0.05), None)

    records = [r for r in caplog.records if "diag_strategy" in r.getMessage()]
    s1_skip = [r for r in records if '"strategy": "S1"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    s2_skip = [r for r in records if '"strategy": "S2"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]

    assert len(s1_skip) == 1
    assert len(s2_skip) == 1
    assert '"reason": "regime_unknown"' in s1_skip[0].getMessage()

    caplog.clear()

    # 2. Test S2 OI Stale via evaluate_symbol
    monkeypatch.setattr(bot_module, "detect_regime", lambda snap, fz: (MarketRegime.TRENDING, RegimeMetrics(None, None, None, None)))
    from tests.strategies.test_s2_volatility_compression import build_realistic_s2_long_snapshot
    s2_snap = build_realistic_s2_long_snapshot()
    stale_oi = replace(s2_snap.derivatives.open_interest_history_5m[-1], event_ts_ms=100, received_ts_ms=100)
    s2_snap.derivatives.open_interest_history_5m[-1] = stale_oi

    with caplog.at_level(logging.INFO):
        await bot.evaluate_symbol(s2_snap, bot.news_engine.build_news_state(1))

    records = [r for r in caplog.records if "diag_strategy" in r.getMessage()]
    s2_stale = [r for r in records if '"strategy": "S2"' in r.getMessage() and '"decision": "rejected"' in r.getMessage() and '"reason": "oi_data_stale"' in r.getMessage()]
    assert len(s2_stale) == 1

    caplog.clear()

    # 3. Test evaluate_symbol with valid regime and fresh OI (should generate candidate)
    s2_snap = build_realistic_s2_long_snapshot()

    # We use SQLite memory DB for accurate testing of get_open_signals etc.
    from app.database.repository import SignalRepository
    bot.repo = SignalRepository(":memory:")
    await bot.repo.connect()

    import asyncio
    bot.outbox = asyncio.Queue()

    with caplog.at_level(logging.INFO):
        await bot.evaluate_symbol(s2_snap, bot.news_engine.build_news_state(1))

    records = [r for r in caplog.records if "s2_candidate_created" in r.getMessage() or ("diag_strategy" in r.getMessage() and '"decision": "candidate"' in r.getMessage())]
    assert len(records) > 0
    await bot.repo.close()
