"""Retirement regression for removed G3 (g3_depth_collapse)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g3_depth_collapse_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g3_depth_collapse" not in cfg
    assert not hasattr(veto, "guard_g3_depth_collapse")
