"""Retirement regression for removed G15 (g15_oi_percentile_extreme)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g15_oi_percentile_extreme_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g15_oi_percentile_extreme" not in cfg
    assert not hasattr(veto, "guard_g15_oi_percentile_extreme")
