from __future__ import annotations

import os

import pytest

from app.core.errors import TradingCredentialsPresentError
from app.safety import (
    FORBIDDEN_CAPABILITIES,
    FORBIDDEN_CREDENTIAL_ENV_VARS,
    assert_capability_not_implemented,
    assert_no_trading_credentials,
    run_all_boot_safety_checks,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in FORBIDDEN_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_no_credentials_passes():
    assert_no_trading_credentials()  # should not raise


def test_binance_api_key_present_raises(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "some-value")
    with pytest.raises(TradingCredentialsPresentError):
        assert_no_trading_credentials()


def test_binance_api_secret_present_raises(monkeypatch):
    monkeypatch.setenv("BINANCE_API_SECRET", "some-value")
    with pytest.raises(TradingCredentialsPresentError):
        assert_no_trading_credentials()


def test_empty_string_credential_does_not_trigger(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "")
    assert_no_trading_credentials()  # empty string is falsy, should pass


def test_assert_capability_not_implemented_raises_for_forbidden_name():
    for cap in FORBIDDEN_CAPABILITIES:
        with pytest.raises(TradingCredentialsPresentError):
            assert_capability_not_implemented(cap)


def test_assert_capability_not_implemented_allows_other_names():
    assert_capability_not_implemented("compute_signal")  # should not raise


def test_run_all_boot_safety_checks_passes_clean():
    run_all_boot_safety_checks()


def test_run_all_boot_safety_checks_raises_dirty(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "leaked")
    with pytest.raises(TradingCredentialsPresentError):
        run_all_boot_safety_checks()
