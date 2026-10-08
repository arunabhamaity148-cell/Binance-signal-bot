from __future__ import annotations
import sqlite3
from scripts.smoke_test import main as smoke_main
from scripts.soak_test import main as soak_main

def test_offline_smoke_runs_signal_pipeline_and_persists_to_separate_journal(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD","1000")
    journal=tmp_path/"smoke-only.sqlite3"
    assert smoke_main(["--symbol","BTCUSDT","--journal",str(journal)])==0
    output=capsys.readouterr().out
    assert "source: synthetic offline fixture" in output
    assert "signals constructed and persisted: 1" in output
    with sqlite3.connect(journal) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]==1

def test_short_soak_script_reports_partial_pending_not_completed(tmp_path,capsys):
    journal=tmp_path/"soak-only.sqlite3"
    rc=soak_main(["--hours","0.0000001","--sample-interval-seconds","0.001","--journal",str(journal)])
    output=capsys.readouterr().out
    assert rc==0
    assert "PARTIAL REPORT" in output
    assert "status: PENDING" in output
    assert "COMPLETED" not in output
    assert journal.is_file()
