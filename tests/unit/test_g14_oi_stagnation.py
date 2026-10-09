"""Retirement regression for removed G14 (g14_oi_stagnation)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g14_oi_stagnation_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g14_oi_stagnation" not in cfg
    assert not hasattr(veto, "guard_g14_oi_stagnation")
