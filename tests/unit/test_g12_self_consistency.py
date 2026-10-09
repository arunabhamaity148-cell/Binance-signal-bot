"""Retirement regression for removed G12 (g12_self_consistency)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g12_self_consistency_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g12_self_consistency" not in cfg
    assert not hasattr(veto, "guard_g12_self_consistency")
