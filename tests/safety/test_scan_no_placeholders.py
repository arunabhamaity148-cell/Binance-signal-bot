from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scan_no_placeholders import run_scan, scan_python_ast, scan_text_patterns


def test_current_repository_passes_placeholder_scan():
    violations = run_scan()
    assert violations == [], f"unexpected placeholder violations: {violations}"


def test_scanner_detects_todo(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("# TODO: fix this later\ndef f():\n    return 1\n")
    violations = scan_text_patterns(bad_file)
    assert any("TODO" in v for v in violations)


def test_scanner_detects_not_implemented_error(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def f():\n    raise NotImplementedError\n")
    violations = scan_text_patterns(bad_file)
    assert any("NotImplementedError" in v for v in violations)


def test_scanner_detects_pass_only_function(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def f():\n    pass\n")
    violations = scan_python_ast(bad_file)
    assert any("pass-only" in v for v in violations)


def test_scanner_allows_pass_only_with_docstring_is_still_flagged(tmp_path):
    """A docstring-only function whose body is otherwise just `pass`
    is still a placeholder and must be flagged."""
    bad_file = tmp_path / "bad.py"
    bad_file.write_text('def f():\n    """does nothing yet"""\n    pass\n')
    violations = scan_python_ast(bad_file)
    assert any("pass-only" in v for v in violations)


def test_scanner_allows_docstring_only_function_without_pass(tmp_path):
    """A function with ONLY a docstring and a real return is fine."""
    good_file = tmp_path / "good.py"
    good_file.write_text('def f():\n    """computes something"""\n    return 1\n')
    violations = scan_python_ast(good_file)
    assert violations == []


def test_scanner_detects_bare_print(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def f():\n    print('debug')\n    return 1\n")
    violations = scan_python_ast(bad_file)
    assert any("print()" in v for v in violations)


def test_scanner_clean_function_has_no_violations(tmp_path):
    good_file = tmp_path / "good.py"
    good_file.write_text("def compute(x):\n    return x * 2\n")
    assert scan_python_ast(good_file) == []
    assert scan_text_patterns(good_file) == []


def test_scanner_detects_stub_word(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("# this is a stub implementation\ndef f():\n    return 1\n")
    violations = scan_text_patterns(bad_file)
    assert any("stub" in v for v in violations)
