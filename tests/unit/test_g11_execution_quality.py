"""Retirement regression for removed G11 (g11_execution_quality)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g11_execution_quality_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g11_execution_quality" not in cfg
    assert not hasattr(veto, "guard_g11_execution_quality")
