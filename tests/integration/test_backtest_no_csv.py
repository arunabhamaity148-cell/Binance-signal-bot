"""Proves run_backtest.py and run_walkforward.py NEVER invent results
and NEVER download data: without --csv, both print NOT RUN and exit 3;
with a missing/unreadable CSV, both still exit 3 rather than crash or
fabricate a report.

Invoked as actual subprocesses (not by importing and calling `main()`
in-process) so the exit code and stdout/stderr are checked exactly as
an operator or CI pipeline would observe them.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_script(script_name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script_name), *args],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# run_backtest.py
# ---------------------------------------------------------------------------


def test_run_backtest_without_csv_prints_not_run_and_exits_3():
    result = _run_script("run_backtest.py")
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_backtest_missing_csv_file_exits_3():
    result = _run_script("run_backtest.py", "--csv", "/tmp/this_file_does_not_exist_xyz.csv")
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_backtest_malformed_csv_exits_3_not_crash(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("not,a,valid,header\n1,2,3,4\n")
    result = _run_script("run_backtest.py", "--csv", str(bad_csv))
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_backtest_valid_csv_runs_and_exits_0_or_2(tmp_path):
    """With a real (if small/synthetic) CSV, the script must actually
    run and produce a numeric exit code reflecting gate pass/fail --
    never exit 3 (NOT RUN) when data WAS supplied and WAS readable."""
    csv_path = tmp_path / "data.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ts_ms", "open", "high", "low", "close", "volume"])
        price = 100_000.0
        for i in range(100):
            c = price + 1
            writer.writerow([1_700_000_000_000 + i * 300_000, price, c + 1, price - 1, c, 10.0])
            price = c
    result = _run_script("run_backtest.py", "--csv", str(csv_path), "--symbol", "BTCUSDT")
    assert result.returncode in (0, 2)
    assert "NOT RUN" not in result.stdout
    assert "BACKTEST COMPLETE" in result.stdout


def test_run_backtest_never_contains_network_fetch_language():
    """A script that genuinely never downloads data should never print
    anything suggesting it fetched from a remote source."""
    result = _run_script("run_backtest.py")
    combined = result.stdout + result.stderr
    for forbidden_phrase in ("downloading", "fetching from", "connecting to binance"):
        assert forbidden_phrase.lower() not in combined.lower()


# ---------------------------------------------------------------------------
# run_walkforward.py
# ---------------------------------------------------------------------------


def test_run_walkforward_without_csv_prints_not_run_and_exits_3():
    result = _run_script("run_walkforward.py")
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_walkforward_missing_csv_file_exits_3():
    result = _run_script("run_walkforward.py", "--csv", "/tmp/this_file_does_not_exist_xyz.csv")
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_walkforward_insufficient_data_exits_3(tmp_path):
    """Too few bars to form min_folds anchored folds -> NOT RUN, exit 3
    (not a crash, not a fabricated partial result)."""
    csv_path = tmp_path / "tiny.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ts_ms", "open", "high", "low", "close", "volume"])
        writer.writerow([1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 10.0])
    result = _run_script("run_walkforward.py", "--csv", str(csv_path))
    assert result.returncode == 3
    assert "NOT RUN" in result.stdout


def test_run_walkforward_sufficient_data_runs_and_reports_holdout(tmp_path):
    csv_path = tmp_path / "data.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ts_ms", "open", "high", "low", "close", "volume"])
        price = 100_000.0
        for i in range(2000):
            c = price + (1 if i % 2 == 0 else -1)
            writer.writerow([1_700_000_000_000 + i * 300_000, price, max(price, c) + 50, min(price, c) - 50, c, 10.0])
            price = c
    result = _run_script("run_walkforward.py", "--csv", str(csv_path))
    assert result.returncode in (0, 2)
    assert "NOT RUN" not in result.stdout
    assert "HOLDOUT" in result.stdout
    assert "never fit on" in result.stdout
