from dataclasses import replace

from app.risk.regime_detector import MarketRegime, detect_regime
from tests.integration.test_pipeline_e2e import build_realistic_s1_snapshot


def test_after_boot_at_least_80_percent_of_symbols_have_known_regime():
    snapshots = [replace(build_realistic_s1_snapshot(), symbol=f"SYN{i}USDT") for i in range(5)]
    results = [detect_regime(snapshot)[0] for snapshot in snapshots]

    known = sum(regime != MarketRegime.UNKNOWN for regime in results)
    assert known >= 4
