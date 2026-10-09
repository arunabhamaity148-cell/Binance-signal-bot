"""Configuration loading.

Loads the seven YAML config files (top20_pairs, system, strategy, veto,
news_sources, risk, delta) into plain dict structures. Validation of required
keys/types/ranges lives in scripts/validate_config.py and is re-run at
boot via `load_and_validate_all()` so a bad config can never silently
start the bot.

No threshold value is hardcoded here — every numeric constant used by
strategies, guards, and the risk engine comes from these files, so a
config edit is the only way to change bot behavior. Config values
marked class F in the YAML comments remain class F; nothing here
"promotes" them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigError
from app.monitoring.diagnostics import configure as configure_diagnostics

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_REQUIRED_FILES = (
    "top20_pairs.yaml",
    "system.yaml",
    "strategy.yaml",
    "veto.yaml",
    "news_sources.yaml",
    "risk.yaml",
    "delta.yaml",
)


@dataclass(frozen=True)
class AppConfig:
    top20_pairs: dict[str, Any]
    system: dict[str, Any]
    strategy: dict[str, Any]
    veto: dict[str, Any]
    news_sources: dict[str, Any]
    risk: dict[str, Any]
    delta: dict[str, Any]
    config_dir: Path

    def pair_config(self, symbol: str) -> dict[str, Any]:
        for entry in self.top20_pairs.get("pairs", []):
            if entry.get("symbol") == symbol:
                return entry
        raise ConfigError(f"symbol {symbol!r} not found in top20_pairs.yaml")

    def enabled_symbols(self) -> list[str]:
        return [
            entry["symbol"]
            for entry in self.top20_pairs.get("pairs", [])
            if entry.get("enabled", False)
        ]

    def symbol_tier(self, symbol: str) -> str:
        tiers = self.risk.get("symbol_tiers", {})
        for tier_name in ("majors", "mid_caps", "small_caps"):
            if symbol in tiers.get(tier_name, []):
                return tier_name
        raise ConfigError(f"symbol {symbol!r} not found in any symbol_tiers bucket")


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"required config file missing: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"config file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a top-level mapping: {path}")
    return data


def load_all(config_dir: Path | None = None) -> AppConfig:
    """Load all seven config files from `config_dir` (defaults to the
    repository's config/ directory). Does NOT perform semantic
    validation beyond "is this valid YAML with a top-level mapping" —
    call `validate_config.run_validation(cfg)` separately (also
    invoked automatically by `load_and_validate_all`).
    """
    directory = config_dir or DEFAULT_CONFIG_DIR
    if not directory.is_dir():
        raise ConfigError(f"config directory does not exist: {directory}")

    missing = [f for f in _REQUIRED_FILES if not (directory / f).exists()]
    if missing:
        raise ConfigError(f"missing required config files: {missing}")

    system = _load_yaml_file(directory / "system.yaml")
    strategy = _load_yaml_file(directory / "strategy.yaml")
    veto = _load_yaml_file(directory / "veto.yaml")
    news_sources = _load_yaml_file(directory / "news_sources.yaml")
    # Environment overrides are intentionally limited to diagnostics; they
    # cannot change signal, risk, exchange, or trading configuration.
    level = os.getenv("DIAGNOSTIC_LEVEL", system.get("diagnostic_level", "summary")).strip().lower()
    if os.getenv("DIAGNOSTIC_MODE", "true").strip().lower() in {"0", "false", "no"}:
        level = "off"
    strategy.setdefault("common", {})["diagnostic_level"] = level
    veto["diagnostic_level"] = level
    news_sources["diagnostic_level"] = level
    system["diagnostic_level"] = level
    configure_diagnostics(level, enabled=system.get("diagnostic_mode", True))
    return AppConfig(
        top20_pairs=_load_yaml_file(directory / "top20_pairs.yaml"), system=system,
        strategy=strategy, veto=veto, news_sources=news_sources,
        risk=_load_yaml_file(directory / "risk.yaml"), delta=_load_yaml_file(directory / "delta.yaml"),
        config_dir=directory,
    )


def load_and_validate_all(config_dir: Path | None = None) -> AppConfig:
    """Load all config and run full semantic validation. Raises
    ConfigError on any validation failure. This is the entry point
    main.py uses at boot."""
    # Imported lazily to avoid a circular import (validate_config also
    # imports AppConfig for type hints).
    from scripts.validate_config import run_validation

    cfg = load_all(config_dir)
    errors = run_validation(cfg)
    if errors:
        joined = "\n  - ".join(errors)
        raise ConfigError(f"config validation failed:\n  - {joined}")
    return cfg


def get_assumed_account_equity_usd() -> float:
    """Read and validate ASSUMED_ACCOUNT_EQUITY_USD from the
    environment. Raises MissingAssumedEquityError if missing,
    non-numeric, or <= 0. There is NO hardcoded fallback anywhere in
    this function or anywhere else in the codebase (spec section 18,
    section 20)."""
    from app.core.errors import MissingAssumedEquityError

    raw = os.environ.get("ASSUMED_ACCOUNT_EQUITY_USD")
    if raw is None or raw.strip() == "":
        raise MissingAssumedEquityError(
            "ASSUMED_ACCOUNT_EQUITY_USD is not set. Refusing to start."
        )
    try:
        value = float(raw)
    except ValueError as exc:
        raise MissingAssumedEquityError(
            f"ASSUMED_ACCOUNT_EQUITY_USD is not numeric: {raw!r}"
        ) from exc
    if value <= 0:
        raise MissingAssumedEquityError(
            f"ASSUMED_ACCOUNT_EQUITY_USD must be > 0, got {value}"
        )
    return value
