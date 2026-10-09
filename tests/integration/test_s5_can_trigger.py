from app.config import load_all
from app.strategies.s5_oi_regime import S5OiRegime
from tests.strategies.test_s5_oi_regime import build_realistic_s5_long_snapshot, _empty_news

CFG = load_all().strategy


def test_s5_can_trigger_from_documented_oi_regime_shift():
    snapshot = build_realistic_s5_long_snapshot()
    assert S5OiRegime().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)
