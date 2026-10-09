"""Retirement regression for removed G7 (g7_news_shock)."""

from pathlib import Path

import yaml

from app.risk import veto


def test_g7_news_shock_is_removed_from_production_stack():
    cfg = yaml.safe_load(Path("config/veto.yaml").read_text())
    assert "g7_news_shock" not in cfg
    assert not hasattr(veto, "guard_g7_news_shock")
