"""Retirement regression for removed G10 (g10_orderbook_instability)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g10_orderbook_instability_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g10_orderbook_instability" not in cfg
    assert not hasattr(veto, "guard_g10_orderbook_instability")
