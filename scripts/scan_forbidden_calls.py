#!/usr/bin/env python3
"""Static scanner: forbidden calls and hardcoded-secret patterns
(spec section 28).

Distinct from scan_signal_only.py (which focuses narrowly on the
trading-capability invariant) - this scanner focuses on general
security hygiene: hardcoded secret-shaped strings, disabled TLS
verification, and use of dangerous dynamic-execution builtins that
have no legitimate purpose in this codebase.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = ["app", "scripts"]
EXCLUDE_DIRS = {"tests"}

ALLOWLISTED_FILES = {
    "scripts/scan_forbidden_calls.py",
    "scripts/scan_signal_only.py",
    "scripts/scan_no_placeholders.py",
    "scripts/validate_config.py",
    "app/core/logging.py",  # defines the redaction key-marker list itself
}

# Patterns that look like an actual embedded secret value (not just a
# reference to the concept of a secret). We look for suspicious
# assignment of a long opaque token literal to a credential-shaped
# variable name.
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|"
    r"private[_-]?key|password|passwd)\s*[:=]\s*[\"'][A-Za-z0-9_\-/+=]{12,}[\"']"
)

_TLS_DISABLE_PATTERNS = [
    re.compile(r"verify\s*=\s*False"),
    re.compile(r"ssl\._create_unverified_context"),
    re.compile(r"CERT_NONE"),
    re.compile(r"check_hostname\s*=\s*False"),
]

_DANGEROUS_BUILTIN_CALLS = {"eval", "exec", "compile", "__import__"}

_FORBIDDEN_ORDER_METHOD_NAMES = {
    "place_order",
    "cancel_order",
    "modify_order",
    "close_position",
    "set_leverage",
    "withdraw",
    "transfer",
}


def scan_text_patterns(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations
    try:
        rel_posix = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel_posix = path.as_posix()
    if rel_posix in ALLOWLISTED_FILES:
        # These files document, in prose, the patterns they scan for
        # (e.g. "hardcoded secret", "TLS verification disabled") -
        # that is metadata about the scanner, not an instance of the
        # violation.
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _SECRET_ASSIGNMENT_PATTERN.search(line):
            violations.append(f"{path}:{lineno}: possible hardcoded secret literal: {line.strip()}")
        for pattern in _TLS_DISABLE_PATTERNS:
            if pattern.search(line):
                violations.append(f"{path}:{lineno}: TLS verification disabled: {line.strip()}")
    return violations


def scan_python_ast(path: Path) -> list[str]:
    violations: list[str] = []
    if path.suffix != ".py":
        return violations
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Only a bare, unqualified call to the builtin name (e.g.
            # `eval(...)`, `compile(...)`) counts as the dangerous
            # builtin. A qualified call like `re.compile(...)` or
            # `ast.compile(...)` is calling a DIFFERENT function that
            # merely shares a name with a builtin and must not be
            # flagged - checking isinstance(func, ast.Name) (not
            # ast.Attribute) enforces exactly that distinction.
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_BUILTIN_CALLS:
                violations.append(f"{path}:{node.lineno}: dangerous dynamic execution call: {func.id}()")

            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_ORDER_METHOD_NAMES:
                violations.append(f"{path}:{node.lineno}: forbidden trading method call: {name}()")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _FORBIDDEN_ORDER_METHOD_NAMES:
                violations.append(f"{path}:{node.lineno}: forbidden trading method defined: {node.name}()")
    return violations


def iter_scan_targets() -> list[Path]:
    targets: list[Path] = []
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLISTED_FILES:
                continue
            targets.append(path)
    return targets


def run_scan() -> list[str]:
    all_violations: list[str] = []
    for path in iter_scan_targets():
        all_violations.extend(scan_text_patterns(path))
        all_violations.extend(scan_python_ast(path))
    return all_violations


def main() -> int:
    violations = run_scan()
    if violations:
        print("FORBIDDEN CALLS SCAN: FAILED", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s) found.", file=sys.stderr)
        return 1
    print("FORBIDDEN CALLS SCAN: PASS (no hardcoded secrets, disabled TLS, or forbidden trading calls found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
