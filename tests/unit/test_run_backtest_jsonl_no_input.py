from __future__ import annotations
import subprocess
import sys
from pathlib import Path

def test_run_backtest_jsonl_without_input_prints_not_run_and_exits_3():
    script=Path(__file__).resolve().parents[2]/"scripts"/"run_backtest_jsonl.py"
    result=subprocess.run([sys.executable,str(script)],text=True,capture_output=True,check=False)
    assert result.returncode==3
    assert "NOT RUN" in result.stdout
