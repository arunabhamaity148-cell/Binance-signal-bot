"""Retirement regression for removed G16 (g16_multi_tf_confluence)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g16_multi_tf_confluence_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g16_multi_tf_confluence" not in cfg
    assert not hasattr(veto, "guard_g16_multi_tf_confluence")
