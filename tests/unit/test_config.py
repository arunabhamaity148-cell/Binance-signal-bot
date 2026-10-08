from __future__ import annotations

import pytest

from app.config import get_assumed_account_equity_usd, load_all, load_and_validate_all
from app.core.errors import ConfigError, MissingAssumedEquityError


def test_load_all_loads_all_six_files():
    cfg = load_all()
    assert cfg.top20_pairs
    assert cfg.system
    assert cfg.strategy
    assert cfg.veto
    assert cfg.news_sources
    assert cfg.risk


def test_load_and_validate_all_passes_on_shipped_config():
    cfg = load_and_validate_all()
    assert cfg is not None


def test_enabled_symbols_returns_20_pairs():
    cfg = load_all()
    symbols = cfg.enabled_symbols()
    assert len(symbols) == 20
    assert "BTCUSDT" in symbols


def test_pair_config_returns_entry():
    cfg = load_all()
    entry = cfg.pair_config("BTCUSDT")
    assert entry["symbol"] == "BTCUSDT"
    assert entry["price_tick"] > 0


def test_pair_config_raises_for_unknown_symbol():
    cfg = load_all()
    with pytest.raises(ConfigError):
        cfg.pair_config("NOTASYMBOL")


def test_symbol_tier_classification():
    cfg = load_all()
    assert cfg.symbol_tier("BTCUSDT") == "majors"
    assert cfg.symbol_tier("SUIUSDT") == "small_caps"


def test_symbol_tier_raises_for_unknown_symbol():
    cfg = load_all()
    with pytest.raises(ConfigError):
        cfg.symbol_tier("NOTASYMBOL")


def test_get_assumed_account_equity_missing_raises(monkeypatch):
    monkeypatch.delenv("ASSUMED_ACCOUNT_EQUITY_USD", raising=False)
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_get_assumed_account_equity_empty_string_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_get_assumed_account_equity_non_numeric_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "not-a-number")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_get_assumed_account_equity_zero_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "0")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_get_assumed_account_equity_negative_raises(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "-100")
    with pytest.raises(MissingAssumedEquityError):
        get_assumed_account_equity_usd()


def test_get_assumed_account_equity_valid_value(monkeypatch):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD", "1000")
    assert get_assumed_account_equity_usd() == 1000.0


def test_no_hardcoded_fallback_in_source():
    """Static guard: read the config.py source and assert there is no
    numeric literal assigned as a fallback for equity anywhere near the
    function. This is a belt-and-suspenders check on top of the
    behavioral tests above."""
    import inspect

    import app.config as config_module

    source = inspect.getsource(config_module.get_assumed_account_equity_usd)
    assert "return 1000" not in source
    assert "= 1000" not in source
