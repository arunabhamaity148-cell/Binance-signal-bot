from app.config import load_all
from app.strategies.s2_volatility_compression import S2VolatilityCompression
from tests.strategies.test_s2_volatility_compression import build_realistic_s2_long_snapshot, _empty_news

CFG = load_all().strategy


def test_s2_can_trigger_from_documented_compression_breakout():
    snapshot = build_realistic_s2_long_snapshot()
    assert S2VolatilityCompression().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)
