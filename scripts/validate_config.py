#!/usr/bin/env python3
"""Validates all seven config YAML files against the schema described in
CONFIG_SCHEMAS.md. Returns a list of human-readable error strings;
an empty list means validation passed.

Used both as a standalone script (`python scripts/validate_config.py`)
and imported by app/config.py's `load_and_validate_all()` so the same
validation runs at every boot, not just in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_VALID_CLASSES = {"A", "B", "C", "D", "E", "F"}
_REQUIRED_STALENESS_KEYS = {
    "kline_ms",
    "aggtrade_ms",
    "depth_ms",
    "bookticker_ms",
    "funding_ms",
    "oi_ms",
    "ratios_ms",
}
_REQUIRED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "BCHUSDT", "LTCUSDT", "DOTUSDT",
    "TRXUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "ZECUSDT",
    "NEARUSDT", "ATOMUSDT",
}


def _err(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_top20_pairs(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pairs = data.get("pairs")
    _err(errors, isinstance(pairs, list), "top20_pairs.yaml: 'pairs' must be a list")
    if not isinstance(pairs, list):
        return errors

    seen_symbols: set[str] = set()
    for entry in pairs:
        if not isinstance(entry, dict):
            errors.append(f"top20_pairs.yaml: pair entry is not a mapping: {entry!r}")
            continue
        symbol = entry.get("symbol")
        _err(errors, isinstance(symbol, str), f"top20_pairs.yaml: pair entry missing valid 'symbol': {entry!r}")
        if isinstance(symbol, str):
            seen_symbols.add(symbol)
        for key in ("enabled",):
            _err(errors, key in entry, f"top20_pairs.yaml: {symbol}: missing key '{key}'")
        for key in ("fee_maker_bps", "fee_taker_bps", "min_qty", "qty_step", "price_tick"):
            val = entry.get(key)
            _err(
                errors,
                isinstance(val, (int, float)) and val > 0,
                f"top20_pairs.yaml: {symbol}: '{key}' must be a positive number, got {val!r}",
            )

    missing_required = _REQUIRED_SYMBOLS - seen_symbols
    _err(
        errors,
        not missing_required,
        f"top20_pairs.yaml: missing required symbols from the 20-pair universe: {sorted(missing_required)}",
    )
    return errors


def validate_system(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _err(errors, data.get("mode") == "signal_only", "system.yaml: 'mode' must be 'signal_only'")
    _err(errors, data.get("fail_closed") is True, "system.yaml: 'fail_closed' must be true")

    forbidden_caps = data.get("forbidden_capabilities")
    _err(errors, isinstance(forbidden_caps, list) and len(forbidden_caps) > 0,
         "system.yaml: 'forbidden_capabilities' must be a non-empty list")
    expected_caps = {"place_order", "cancel_order", "modify_order", "close_position",
                      "set_leverage", "withdraw", "transfer"}
    if isinstance(forbidden_caps, list):
        missing = expected_caps - set(forbidden_caps)
        _err(errors, not missing, f"system.yaml: 'forbidden_capabilities' missing entries: {missing}")

    forbidden_creds = data.get("forbidden_credentials")
    _err(errors, isinstance(forbidden_creds, list) and "BINANCE_API_KEY" in forbidden_creds,
         "system.yaml: 'forbidden_credentials' must include BINANCE_API_KEY")

    _err(errors, isinstance(data.get("binance_base_url"), str) and data["binance_base_url"].startswith("https://"),
         "system.yaml: 'binance_base_url' must be an https:// URL")
    _err(errors, data.get("binance_env") in ("mainnet", "testnet"),
         "system.yaml: 'binance_env' must be 'mainnet' or 'testnet'")
    _err(errors, isinstance(data.get("delta_enabled"), bool),
         "system.yaml: 'delta_enabled' must be a boolean")

    staleness = data.get("staleness_budget_ms", {})
    _err(errors, isinstance(staleness, dict), "system.yaml: 'staleness_budget_ms' must be a mapping")
    if isinstance(staleness, dict):
        missing_keys = _REQUIRED_STALENESS_KEYS - set(staleness.keys())
        _err(errors, not missing_keys, f"system.yaml: staleness_budget_ms missing keys: {missing_keys}")
        for k, v in staleness.items():
            _err(errors, isinstance(v, (int, float)) and v > 0,
                 f"system.yaml: staleness_budget_ms.{k} must be a positive number")

    boot_checks = data.get("boot_checks", {})
    _err(errors, isinstance(boot_checks, dict), "system.yaml: 'boot_checks' must be a mapping")
    if isinstance(boot_checks, dict):
        min_sym = boot_checks.get("min_symbols_validated")
        _err(errors, isinstance(min_sym, int) and 0 < min_sym <= 20,
             "system.yaml: boot_checks.min_symbols_validated must be an int in (0, 20]")

    return errors


def validate_strategy(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_sections = {
        "common", "s1_liquidity_sweep", "s2_volatility_compression",
        "s3_funding_crowding", "s4_oi_trend", "s5_oi_regime", "consensus",
    }
    missing = required_sections - set(data.keys())
    _err(errors, not missing, f"strategy.yaml: missing required sections: {missing}")

    common = data.get("common", {})
    _err(errors, isinstance(common.get("atr_period"), int) and common.get("atr_period", 0) > 0,
         "strategy.yaml: common.atr_period must be a positive int")
    _err(errors, isinstance(common.get("min_candles"), int) and common.get("min_candles", 0) > 0,
         "strategy.yaml: common.min_candles must be a positive int")
    _err(errors, isinstance(common.get("min_stop_cost_multiple"), (int, float))
         and common.get("min_stop_cost_multiple", 0) > 0,
         "strategy.yaml: common.min_stop_cost_multiple must be a positive number")
    _err(errors, isinstance(common.get("max_entry_zone_width_r_multiple"), (int, float))
         and common.get("max_entry_zone_width_r_multiple", 0) > 0,
         "strategy.yaml: common.max_entry_zone_width_r_multiple must be a positive number")

    for strat_key in ("s1_liquidity_sweep", "s2_volatility_compression",
                       "s3_funding_crowding", "s4_oi_trend", "s5_oi_regime"):
        section = data.get(strat_key, {})
        _err(errors, isinstance(section, dict) and len(section) > 0,
             f"strategy.yaml: '{strat_key}' must be a non-empty mapping")
        allowed = section.get("allowed_regimes")
        _err(errors, isinstance(allowed, list) and allowed and all(
            value in {"TRENDING", "RANGING", "HIGH_VOLATILITY"} for value in allowed
        ), f"strategy.yaml: {strat_key}.allowed_regimes must contain valid regimes")
        tp_key = "tp_r_multiples"
        tps = section.get(tp_key)
        if tps is not None:
            _err(errors, isinstance(tps, list) and len(tps) == 4 and all(isinstance(x, (int, float)) for x in tps),
                 f"strategy.yaml: {strat_key}.{tp_key} must be a list of 4 numbers")
            if isinstance(tps, list) and len(tps) == 4:
                _err(errors, tps == sorted(tps),
                     f"strategy.yaml: {strat_key}.{tp_key} must be strictly ascending")

    consensus = data.get("consensus", {})
    for k in ("grade_a_plus_min_conf", "grade_a_min_conf", "grade_b_min_conf"):
        v = consensus.get(k)
        _err(errors, isinstance(v, (int, float)) and 0 <= v <= 1,
             f"strategy.yaml: consensus.{k} must be in [0, 1]")

    return errors


def validate_veto(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_guards = {f"g{i}_" for i in range(1, 17)}
    present_prefixes = {k.split("_")[0] + "_" for k in data.keys()}
    for prefix in required_guards:
        _err(errors, any(k.startswith(prefix) for k in data.keys()),
             f"veto.yaml: missing guard section with prefix '{prefix}'")

    for guard_key, section in data.items():
        if not isinstance(section, dict):
            errors.append(f"veto.yaml: guard section '{guard_key}' must be a mapping")
            continue
        severity = section.get("severity")
        _err(errors, severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"),
             f"veto.yaml: {guard_key}.severity must be one of CRITICAL/HIGH/MEDIUM/LOW, got {severity!r}")

    g16 = data.get("g16_multi_tf_confluence", {})
    _err(errors, isinstance(g16.get("enabled"), bool), "veto.yaml: g16_multi_tf_confluence.enabled must be boolean")
    for key in ("htf_1h_ema_fast", "htf_1h_ema_slow", "htf_4h_structure_bars"):
        _err(errors, isinstance(g16.get(key), int) and g16[key] > 0,
             f"veto.yaml: g16_multi_tf_confluence.{key} must be a positive int")

    return errors


def validate_news_sources(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for tier_key, tier_num in (("tier_1", 1), ("tier_2", 2), ("tier_3", 3)):
        sources = data.get(tier_key)
        _err(errors, isinstance(sources, list) and len(sources) > 0,
             f"news_sources.yaml: '{tier_key}' must be a non-empty list")
        if isinstance(sources, list):
            for src in sources:
                _err(errors, isinstance(src, dict) and "name" in src and "url" in src,
                     f"news_sources.yaml: {tier_key} entry missing name/url: {src!r}")
                _err(errors, isinstance(src, dict) and 0 <= src.get("credibility_weight", -1) <= 1,
                     f"news_sources.yaml: {tier_key} entry credibility_weight must be in [0,1]: {src!r}")

    half_life = data.get("category_half_life_hours", {})
    required_categories = {
        "regulatory", "macro", "hack", "listing", "delisting",
        "etf", "liquidation", "outage", "other",
    }
    missing_cats = required_categories - set(half_life.keys())
    _err(errors, not missing_cats, f"news_sources.yaml: category_half_life_hours missing: {missing_cats}")

    corroboration = data.get("corroboration", {})
    _err(errors, corroboration.get("tier3_max_severity") == "MEDIUM",
         "news_sources.yaml: corroboration.tier3_max_severity must be MEDIUM (Tier-3 can never reach HIGH/CRITICAL)")
    global_block = corroboration.get("global_block_requires", {})
    _err(errors, global_block.get("tier2_min_independent_sources") == 2,
         "news_sources.yaml: corroboration.global_block_requires.tier2_min_independent_sources must be 2")

    return errors


def validate_risk(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _err(errors, "reference_equity_usdt" not in data,
         "risk.yaml: must NOT contain 'reference_equity_usdt' - equity comes only from ASSUMED_ACCOUNT_EQUITY_USD env var")

    _err(errors, isinstance(data.get("risk_per_trade_pct"), (int, float)) and 0 < data["risk_per_trade_pct"] <= 5,
         "risk.yaml: risk_per_trade_pct must be in (0, 5]")
    _err(errors, isinstance(data.get("min_rr_tp2"), (int, float)) and data["min_rr_tp2"] > 0,
         "risk.yaml: min_rr_tp2 must be positive")

    assumptions = data.get("paper_trading_assumptions")
    _err(errors, isinstance(assumptions, dict),
         "risk.yaml: paper_trading_assumptions must be a mapping")
    if isinstance(assumptions, dict):
        expected = {
            "account_capital_inr": 200000,
            "account_capital_usd": 2400,
            "risk_per_trade_inr": 5000,
            "risk_per_trade_pct": 2.5,
            "assumed_leverage": 10,
            "mode": "paper",
        }
        for key, value in expected.items():
            _err(errors, assumptions.get(key) == value,
                 f"risk.yaml: paper_trading_assumptions.{key} must be {value!r}")

    partials = data.get("partial_exit_fractions")
    _err(errors, isinstance(partials, list) and len(partials) == 4,
         "risk.yaml: partial_exit_fractions must be a list of 4 values")
    if isinstance(partials, list) and len(partials) == 4:
        total = sum(partials)
        _err(errors, abs(total - 1.0) < 1e-9,
             f"risk.yaml: partial_exit_fractions must sum to 1.0, got {total}")

    tiers = data.get("symbol_tiers", {})
    for tier_name in ("majors", "mid_caps", "small_caps"):
        _err(errors, isinstance(tiers.get(tier_name), list),
             f"risk.yaml: symbol_tiers.{tier_name} must be a list")

    all_tiered: list[str] = []
    for tier_name in ("majors", "mid_caps", "small_caps"):
        all_tiered.extend(tiers.get(tier_name, []))
    missing_from_tiers = _REQUIRED_SYMBOLS - set(all_tiered)
    _err(errors, not missing_from_tiers,
         f"risk.yaml: symbol_tiers missing symbols from the 20-pair universe: {sorted(missing_from_tiers)}")
    duplicates = [s for s in set(all_tiered) if all_tiered.count(s) > 1]
    _err(errors, not duplicates, f"risk.yaml: symbol_tiers has symbols in more than one tier: {duplicates}")

    expiry = data.get("expiry_per_grade", {})
    for grade in ("A+", "A", "B"):
        _err(errors, isinstance(expiry.get(grade), (int, float)) and expiry.get(grade, 0) > 0,
             f"risk.yaml: expiry_per_grade['{grade}'] must be a positive number")

    return errors


def validate_delta(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    section = data.get("delta")
    _err(errors, isinstance(section, dict), "delta.yaml: 'delta' must be a mapping")
    if not isinstance(section, dict):
        return errors
    _err(errors, section.get("enabled") is True, "delta.yaml: delta.enabled must be true")
    _err(errors, isinstance(section.get("base_url"), str) and section["base_url"].startswith("https://"),
         "delta.yaml: delta.base_url must be an https:// URL")
    _err(errors, section.get("products_path") == "/v2/products",
         "delta.yaml: delta.products_path must be /v2/products")
    ttl = section.get("products_cache_ttl_seconds")
    _err(errors, isinstance(ttl, (int, float)) and ttl > 0,
         "delta.yaml: delta.products_cache_ttl_seconds must be positive")
    fees = section.get("fees", {})
    _err(errors, isinstance(fees, dict), "delta.yaml: delta.fees must be a mapping")
    if isinstance(fees, dict):
        for key in ("maker_pct", "taker_pct", "gst_multiplier"):
            _err(errors, isinstance(fees.get(key), (int, float)) and fees[key] > 0,
                 f"delta.yaml: delta.fees.{key} must be positive")
    notes = section.get("order_notes", {})
    _err(errors, isinstance(notes, dict), "delta.yaml: delta.order_notes must be a mapping")
    if isinstance(notes, dict):
        _err(errors, notes.get("sl_tp_are_separate_orders") is True,
             "delta.yaml: delta.order_notes.sl_tp_are_separate_orders must be true")
        _err(errors, notes.get("reduce_only_required") is True,
             "delta.yaml: delta.order_notes.reduce_only_required must be true")
    return errors


def validate_cross_file_consistency(cfg) -> list[str]:
    """Checks that MUST be identical across two config files.

    strategy.yaml's s5_oi_regime.min_rr_tp2 is a duplicate of
    risk.yaml's min_rr_tp2 (see the comment in config/strategy.yaml for
    why the duplication exists: every strategy's evaluate() receives
    only the strategy.yaml section, and S5 needs this value per
    STRATEGIES_SPEC.md). Duplication without an enforced-equal check
    would silently drift; this check is that enforcement.
    """
    errors: list[str] = []
    s5_rr = cfg.strategy.get("s5_oi_regime", {}).get("min_rr_tp2")
    risk_rr = cfg.risk.get("min_rr_tp2")
    if s5_rr != risk_rr:
        errors.append(
            f"cross-file consistency: strategy.yaml s5_oi_regime.min_rr_tp2 "
            f"({s5_rr!r}) must equal risk.yaml min_rr_tp2 ({risk_rr!r})"
        )
    return errors


def run_validation(cfg) -> list[str]:
    """Accepts an app.config.AppConfig instance. Returns a flat list
    of error strings across all seven files."""
    errors: list[str] = []
    errors.extend(validate_top20_pairs(cfg.top20_pairs))
    errors.extend(validate_system(cfg.system))
    errors.extend(validate_strategy(cfg.strategy))
    errors.extend(validate_veto(cfg.veto))
    errors.extend(validate_news_sources(cfg.news_sources))
    errors.extend(validate_risk(cfg.risk))
    errors.extend(validate_delta(cfg.delta))
    errors.extend(validate_cross_file_consistency(cfg))
    return errors


def main() -> int:
    from app.config import load_all

    try:
        cfg = load_all()
    except Exception as exc:  # noqa: BLE001 - top-level script boundary
        print(f"CONFIG VALIDATION: FAILED to load config files: {exc}", file=sys.stderr)
        return 1

    errors = run_validation(cfg)
    if errors:
        print("CONFIG VALIDATION: FAILED", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"\n{len(errors)} error(s) found.", file=sys.stderr)
        return 1

    print("CONFIG VALIDATION: PASS (all seven config files valid)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
