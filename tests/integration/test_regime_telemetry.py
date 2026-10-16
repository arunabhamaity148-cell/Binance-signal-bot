import pytest
from app.config import load_all
from app.bot import SignalBot
from app.risk.regime_detector import MarketRegime
from app.core.models import MarketSnapshot
from types import SimpleNamespace
import logging

class _Closable:
    async def close(self): pass

class _News:
    def build_news_state(self, _stamp):
        return SimpleNamespace(active_for=lambda _symbol: ())

class _Cache:
    def __init__(self, regime):
        self.regime = regime
        self.data = {"BTCUSDT": {"error": None}}
    def get_snapshot(self, symbol):
        # We need a snapshot that detect_regime can process
        from app.core.models import MarketSnapshot, SymbolKlines, DerivativesState
        # For detect_regime, it reads klines_for("1h"), klines_for("5m"), and derivatives
        return SimpleNamespace(symbol=symbol, snapshot_version="test", klines_for=lambda tf: [], derivatives=None)
    def is_snapshot_ready(self, _symbol): return True
    def snapshot_missing_components(self, _symbol): return []

@pytest.mark.asyncio
async def test_evaluate_symbol_with_unknown_regime_emits_skipped(monkeypatch, caplog):
    cfg = load_all()
    bot = SignalBot(cfg, 2400.0, rest_client=_Closable(), repository=_Closable())
    bot.symbols = ["BTCUSDT"]
    bot.news_engine = _News()

    # Mock detect_regime
    import app.bot as bot_module
    from app.risk.regime_detector import RegimeMetrics
    monkeypatch.setattr(bot_module, "detect_regime", lambda snap, fz: (MarketRegime.UNKNOWN, RegimeMetrics(None, None, None, None)))

    # Configure diagnostics to log
    import app.monitoring.diagnostics as diagnostics
    diagnostics.configure("summary")

    with caplog.at_level(logging.INFO):
        from app.core.models import MarketSnapshot
        await bot.evaluate_symbol(MarketSnapshot(symbol="BTCUSDT", snapshot_version="test-v1", as_of_ts_ms=1, klines={}, orderbook=None, taker_flow=None, derivatives=None, feed_health={}, price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=0.02, fee_taker_bps=0.05), None)

    s1_skip = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S1"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    assert len(s1_skip) == 1
    assert '"reason": "regime_unknown"' in s1_skip[0].getMessage()

    s2_skip = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S2"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    assert len(s2_skip) == 1
    assert '"reason": "regime_unknown"' in s2_skip[0].getMessage()

@pytest.mark.asyncio
async def test_evaluate_symbol_with_regime_not_allowed(monkeypatch, caplog):
    cfg = load_all()
    bot = SignalBot(cfg, 2400.0, rest_client=_Closable(), repository=_Closable())
    bot.symbols = ["BTCUSDT"]
    bot.news_engine = _News()

    # Mock detect_regime to HIGH_VOLATILITY, which S2, S4 don't allow
    import app.bot as bot_module
    from app.risk.regime_detector import RegimeMetrics
    monkeypatch.setattr(bot_module, "detect_regime", lambda snap, fz: (MarketRegime.HIGH_VOLATILITY, RegimeMetrics(None, None, None, None)))

    import app.monitoring.diagnostics as diagnostics
    diagnostics.configure("summary")

    with caplog.at_level(logging.INFO):
        # snapshot needs to be a real MarketSnapshot or support evaluate() logic correctly, but generate_candidates uses it
        # Actually generate_candidates_at_snapshot handles the skipped logic, which doesn't need to evaluate inside
        from app.core.models import MarketSnapshot
        await bot.evaluate_symbol(MarketSnapshot(symbol="BTCUSDT", snapshot_version="test-v1", as_of_ts_ms=1, klines={}, orderbook=None, taker_flow=None, derivatives=None, feed_health={}, price_tick=0.1, qty_step=0.001, min_qty=0.001, fee_maker_bps=0.02, fee_taker_bps=0.05), None)

    s2_skip = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S2"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    assert len(s2_skip) == 1
    assert '"reason": "regime_not_allowed"' in s2_skip[0].getMessage()

    s4_skip = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S4"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    assert len(s4_skip) == 1
    assert '"reason": "regime_not_allowed"' in s4_skip[0].getMessage()

    # S3 allows HIGH_VOLATILITY, so it shouldn't be skipped by regime
    s3_skip = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S3"' in r.getMessage() and '"decision": "skipped"' in r.getMessage()]
    assert len(s3_skip) == 0
