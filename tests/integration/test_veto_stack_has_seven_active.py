"""The production configuration contains exactly seven veto guards."""

from pathlib import Path

import yaml


ACTIVE = {
    "g1_data_integrity", "g2_feed_health", "g4_spread_explosion",
    "g5_oi_anomaly", "g6_funding_extreme", "g8_volatility_flash",
    "g9_btc_regime",
}
REMOVED = {
    "g3_depth_collapse", "g7_news_shock", "g10_orderbook_instability",
    "g11_execution_quality", "g12_self_consistency", "g13_oi_divergence",
    "g14_oi_stagnation", "g15_oi_percentile_extreme", "g16_multi_tf_confluence",
}


def _config():
    return yaml.safe_load(Path("config/veto.yaml").read_text())


def test_veto_stack_has_exactly_seven_active_guards():
    cfg = _config()
    configured = {k for k, v in cfg.items() if isinstance(v, dict)}
    assert configured == ACTIVE
    assert {k for k, v in cfg.items() if isinstance(v, dict) and v.get("enabled")} == ACTIVE


def test_removed_veto_sections_are_absent():
    assert not (set(_config()) & REMOVED)
