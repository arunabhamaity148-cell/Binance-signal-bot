#!/usr/bin/env python3
"""Static scanner: enforces the signal-only invariant.

Fails (non-zero exit) the build if any forbidden trading capability
name, order-placement function, or trading credential pattern is found
anywhere in the production source tree (app/, scripts/, config/), with
tests/ excluded since tests may legitimately reference forbidden
strings as literals to assert this very scanner catches them.

This scanner is intentionally conservative: it treats the mere
appearance of a forbidden identifier as a name (function definition,
call, or string literal referencing an order-placement operation) as a
violation, on the theory that a signal-only bot should have zero
legitimate reason to even mention these operations outside of safety
checks, documentation, or comments that explicitly disclaim them (this
scanner is itself an explicit exception since it must reference the
list of forbidden terms; see ALLOWLISTED_FILES below).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = ["app", "scripts", "config"]

# Files that must be allowed to reference these terms because they ARE
# the enforcement mechanism, the safety module, or documentation about
# the invariant. Every other production file is scanned strictly.
ALLOWLISTED_FILES = {
    "scripts/scan_signal_only.py",
    "scripts/scan_forbidden_calls.py",
    "scripts/scan_no_placeholders.py",
    "scripts/validate_config.py",  # validates that forbidden-term lists are PRESENT in config; references them as data, not as implementations
    "app/safety.py",
    "config/system.yaml",
}

FORBIDDEN_CAPABILITY_PATTERNS = [
    r"\bplace_order\b",
    r"\bcancel_order\b",
    r"\bmodify_order\b",
    r"\bclose_position\b",
    r"\bset_leverage\b",
    r"\bwithdraw\b",
    r"\btransfer\b",
]

FORBIDDEN_CREDENTIAL_PATTERNS = [
    r"\bBINANCE_API_KEY\b",
    r"\bBINANCE_API_SECRET\b",
]

# Binance trading (non-read-only) REST endpoint path fragments that
# must never appear anywhere in the codebase.
FORBIDDEN_TRADING_ENDPOINTS = [
    r"/fapi/v1/order\b",
    r"/fapi/v1/leverage\b",
    r"/fapi/v1/positionSide\b",
    r"/fapi/v1/marginType\b",
    r"/fapi/v1/allOpenOrders\b",
    r"/sapi/v1/capital/withdraw",
    r"/sapi/v1/futures/transfer",
]

_ALL_PATTERNS = (
    FORBIDDEN_CAPABILITY_PATTERNS
    + FORBIDDEN_CREDENTIAL_PATTERNS
    + FORBIDDEN_TRADING_ENDPOINTS
)
_COMPILED = [re.compile(p) for p in _ALL_PATTERNS]


def scan_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in _COMPILED:
            if pattern.search(line):
                violations.append(f"{path}:{lineno}: matched {pattern.pattern!r}: {line.strip()}")
    return violations


def iter_scan_targets() -> list[Path]:
    targets: list[Path] = []
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".yaml", ".yml"):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in ALLOWLISTED_FILES:
                    continue
                targets.append(path)
    return targets


def run_scan() -> list[str]:
    all_violations: list[str] = []
    for path in iter_scan_targets():
        all_violations.extend(scan_file(path))
    return all_violations


def main() -> int:
    violations = run_scan()
    if violations:
        print("SIGNAL-ONLY SCAN: FAILED", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s) found.", file=sys.stderr)
        return 1
    print("SIGNAL-ONLY SCAN: PASS (no forbidden capabilities, credentials, or trading endpoints found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
