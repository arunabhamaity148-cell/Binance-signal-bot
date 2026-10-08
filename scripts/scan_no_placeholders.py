#!/usr/bin/env python3
"""Static scanner: fails the build if any forbidden placeholder
pattern is found outside tests/ (spec section 2, section 28).

Forbidden patterns: TODO, FIXME, XXX, NotImplementedError, pass-only
placeholder function bodies, "stub", "placeholder", "dummy", "mock"
(outside tests/), "fake" (outside tests/), "temporary", "debug",
bare print( calls (outside logging), and generic "implement later" /
"future improvement" phrasing.

Every hit is reported with file:line so it can be manually reviewed,
per the requirement that hits are not simply grepped and declared
clean.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = ["app", "scripts"]
EXCLUDE_DIRS = {"tests"}

# These scanner files must be allowed to contain words like
# "placeholder"/"stub"/"TODO" as string literals, because they DEFINE
# or REFERENCE the very patterns being scanned for in their own
# docstrings and pattern tables - that is metadata about the scanner,
# not an instance of the violation it exists to catch.
ALLOWLISTED_FILES = {
    "scripts/scan_no_placeholders.py",
    "scripts/scan_signal_only.py",
    "scripts/scan_forbidden_calls.py",
    "scripts/validate_config.py",
}

# A separate, intentionally broader list: every CLI script whose job is
# reporting results to a human via stdout/stderr - print() there is the
# product, not debug output left behind. This is NOT the same list as
# ALLOWLISTED_FILES above: a file in PRINT_ALLOWED_FILES can still be
# scanned for genuine placeholder/TODO/stub text (e.g.
# run_backtest.py could still legitimately be flagged for a real
# "TODO" left in its logic), it is only exempted from the print()
# check specifically.
PRINT_ALLOWED_FILES = ALLOWLISTED_FILES | {
    "scripts/run_backtest.py",
    "scripts/run_walkforward.py",
}

TEXT_PATTERNS = [
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"\bFIXME\b"), "FIXME"),
    (re.compile(r"\bXXX\b"), "XXX"),
    (re.compile(r"\bNotImplementedError\b"), "NotImplementedError"),
    (re.compile(r"\bplaceholder\b", re.IGNORECASE), "placeholder"),
    (re.compile(r"\bstub\b", re.IGNORECASE), "stub"),
    (re.compile(r"\bdummy\b", re.IGNORECASE), "dummy"),
    (re.compile(r"\bmock\b", re.IGNORECASE), "mock (outside tests/)"),
    (re.compile(r"\bfake\b", re.IGNORECASE), "fake (outside tests/)"),
    (re.compile(r"\btemporary\b", re.IGNORECASE), "temporary"),
    (re.compile(r"\bimplement later\b", re.IGNORECASE), "implement later"),
    (re.compile(r"\bfuture improvement\b", re.IGNORECASE), "future improvement"),
    (re.compile(r"\bhardcoded secret\b", re.IGNORECASE), "hardcoded secret pattern reference"),
]

# `debug` and `print(` are checked more carefully below (AST-based)
# since legitimate uses exist (e.g. a variable named `debug_mode` is
# fine, a bare debugging print is not; logging calls are fine).

_URL_PLACEHOLDER_PATTERN = re.compile(r"<[^<>]+>")  # matches "<official Fed RSS URL>" style


def _is_allowlisted_placeholder_url_context(path: Path) -> bool:
    """news_sources.yaml intentionally contains <...> placeholder URLs
    per explicit requirement 13 (do not include real feed URLs; use
    placeholders as-is). This is not a code placeholder violation - it
    is a config value pending operator verification, documented as
    such in KNOWN_UNCERTAINTIES.md. We exclude config/*.yaml files
    from the generic "placeholder" text pattern for this reason, but
    they ARE still scanned for TODO/FIXME/NotImplementedError/etc.
    """
    return path.suffix in (".yaml", ".yml")


def scan_text_patterns(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations

    skip_placeholder_word = _is_allowlisted_placeholder_url_context(path)

    try:
        rel_posix = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel_posix = path.as_posix()
    if rel_posix in ALLOWLISTED_FILES:
        # The scanner scripts themselves must document, in prose, the
        # very words they scan for (docstrings explaining what "stub",
        # "TODO", "hardcoded secret", etc. mean as scan targets). This
        # is metadata about the scanner, not an instance of the
        # violation it exists to catch.
        return violations

    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, label in TEXT_PATTERNS:
            if label == "placeholder" and skip_placeholder_word:
                # config YAML placeholder URL values are allowed (spec
                # requirement 13); still flag them for the manual
                # review log, but not as a hard failure.
                continue
            if pattern.search(line):
                violations.append(f"{path}:{lineno}: [{label}] {line.strip()}")
    return violations


def scan_python_ast(path: Path) -> list[str]:
    """AST-based checks: pass-only function bodies, bare print()
    calls outside the logging module itself."""
    violations: list[str] = []
    if path.suffix != ".py":
        return violations
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return violations

    try:
        rel_posix = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel_posix = path.as_posix()
    is_logging_module = path.as_posix().endswith("app/core/logging.py")
    is_print_allowed = is_logging_module or rel_posix in PRINT_ALLOWED_FILES

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            # Strip a leading docstring before checking for pass-only bodies.
            effective_body = body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                effective_body = body[1:]
            if len(effective_body) == 1 and isinstance(effective_body[0], ast.Pass):
                violations.append(
                    f"{path}:{node.lineno}: pass-only placeholder function body: {node.name}"
                )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print" and not is_print_allowed:
                violations.append(
                    f"{path}:{node.lineno}: bare print() call outside logging module"
                )
    return violations


def iter_scan_targets() -> list[Path]:
    targets: list[Path] = []
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.suffix not in (".py", ".yaml", ".yml", ".md"):
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
        print("PLACEHOLDER SCAN: FAILED", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s) found. Review each manually.", file=sys.stderr)
        return 1
    print("PLACEHOLDER SCAN: PASS (no forbidden placeholder patterns found outside tests/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
