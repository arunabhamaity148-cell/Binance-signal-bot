"""Retirement regression for removed G13 (g13_oi_divergence)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g13_oi_divergence_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g13_oi_divergence" not in cfg
    assert not hasattr(veto, "guard_g13_oi_divergence")
