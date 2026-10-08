from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scan_signal_only import run_scan, scan_file


def test_current_repository_passes_signal_only_scan():
    """The actual, shipped repository must have zero violations."""
    violations = run_scan()
    assert violations == [], f"unexpected signal-only violations: {violations}"


def test_scanner_detects_forbidden_capability_name(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def place_order():\n    pass\n")
    violations = scan_file(bad_file)
    assert any("place_order" in v for v in violations)


def test_scanner_detects_forbidden_credential_reference(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("import os\nkey = os.environ['BINANCE_API_KEY']\n")
    violations = scan_file(bad_file)
    assert any("BINANCE_API_KEY" in v for v in violations)


def test_scanner_detects_forbidden_trading_endpoint(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text('url = "/fapi/v1/order"\n')
    violations = scan_file(bad_file)
    assert any("order" in v for v in violations)


def test_scanner_clean_file_has_no_violations(tmp_path):
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("def compute_signal():\n    return 42\n")
    violations = scan_file(clean_file)
    assert violations == []


def test_scanner_detects_withdraw_and_transfer(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def withdraw():\n    pass\ndef transfer():\n    pass\n")
    violations = scan_file(bad_file)
    assert any("withdraw" in v for v in violations)
    assert any("transfer" in v for v in violations)
