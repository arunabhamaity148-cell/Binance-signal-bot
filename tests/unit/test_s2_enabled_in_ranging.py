from types import SimpleNamespace

import app.backtest.engine as engine
from app.risk.regime_detector import MarketRegime


def test_s2_is_enabled_in_ranging_and_trending_but_not_high_volatility(monkeypatch):
    calls = []

    def evaluate(snapshot, news_state, strategy_cfg):
        calls.append(True)
        return []

    fake_s2 = SimpleNamespace(strategy_id="S2", evaluate=evaluate)
    monkeypatch.setattr(engine, "all_strategies", lambda: [fake_s2])
    cfg = {"s2_volatility_compression": {"allowed_regimes": ["TRENDING", "RANGING"]}}

    engine.generate_candidates_at_snapshot(SimpleNamespace(), None, cfg, regime=MarketRegime.RANGING)
    engine.generate_candidates_at_snapshot(SimpleNamespace(), None, cfg, regime=MarketRegime.TRENDING)
    engine.generate_candidates_at_snapshot(SimpleNamespace(), None, cfg, regime=MarketRegime.HIGH_VOLATILITY)

    assert len(calls) == 2
