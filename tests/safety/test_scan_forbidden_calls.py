from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scan_forbidden_calls import run_scan, scan_python_ast, scan_text_patterns


def test_current_repository_passes_forbidden_calls_scan():
    violations = run_scan()
    assert violations == [], f"unexpected forbidden-call violations: {violations}"


def test_scanner_detects_hardcoded_secret_literal(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text('api_key = "sk_test_FAKE_KEY_FOR_TESTING_ONLY_12345"\n')
    violations = scan_text_patterns(bad_file)
    assert any("hardcoded secret" in v for v in violations)


def test_scanner_detects_disabled_tls_verification(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("import httpx\nclient = httpx.Client(verify=False)\n")
    violations = scan_text_patterns(bad_file)
    assert any("TLS verification disabled" in v for v in violations)


def test_scanner_detects_eval_call(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("x = eval('1+1')\n")
    violations = scan_python_ast(bad_file)
    assert any("eval" in v for v in violations)


def test_scanner_detects_exec_call(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("exec('print(1)')\n")
    violations = scan_python_ast(bad_file)
    assert any("exec" in v for v in violations)


def test_scanner_detects_forbidden_trading_method_definition(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("class Client:\n    def place_order(self):\n        pass\n")
    violations = scan_python_ast(bad_file)
    assert any("place_order" in v for v in violations)


def test_scanner_clean_file_has_no_violations(tmp_path):
    good_file = tmp_path / "good.py"
    good_file.write_text("def compute(x):\n    return x + 1\n")
    assert scan_text_patterns(good_file) == []
    assert scan_python_ast(good_file) == []


def test_scanner_short_string_not_flagged_as_secret(tmp_path):
    """A short, clearly non-secret string assigned to a similarly named
    variable should not trip the heuristic (avoids drowning real
    findings in false positives)."""
    good_file = tmp_path / "good.py"
    good_file.write_text('password_field_label = "Password"\n')
    violations = scan_text_patterns(good_file)
    assert violations == []
