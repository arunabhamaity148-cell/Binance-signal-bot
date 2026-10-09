from __future__ import annotations

from app.config import load_and_validate_all


ACTIVE = {"G1", "G2", "G4", "G5", "G6", "G8", "G9"}
DISABLED = {"G3", "G7", "G10", "G11", "G12", "G13", "G14", "G15", "G16"}
CONFIG_KEYS = {
    "G1": "g1_data_integrity",
    "G2": "g2_feed_health",
    "G3": "g3_depth_collapse",
    "G4": "g4_spread_explosion",
    "G5": "g5_oi_anomaly",
    "G6": "g6_funding_extreme",
    "G7": "g7_news_shock",
    "G8": "g8_volatility_flash",
    "G9": "g9_btc_regime",
    "G10": "g10_orderbook_instability",
    "G11": "g11_execution_quality",
    "G12": "g12_self_consistency",
    "G13": "g13_oi_divergence",
    "G14": "g14_oi_stagnation",
    "G15": "g15_oi_percentile_extreme",
    "G16": "g16_multi_tf_confluence",
}


def test_veto_stack_has_exactly_seven_active_guards():
    cfg = load_and_validate_all()
    enabled = {name for name, key in CONFIG_KEYS.items() if cfg.veto[key].get("enabled", True)}

    assert enabled == ACTIVE
    assert len(enabled) == 7
    assert DISABLED == set(CONFIG_KEYS) - enabled
    assert all(cfg.veto[CONFIG_KEYS[name]].get("enabled") is True for name in ACTIVE)
    assert all(cfg.veto[CONFIG_KEYS[name]].get("enabled") is False for name in DISABLED)
