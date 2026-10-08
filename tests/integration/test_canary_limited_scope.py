from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from app.config import load_all
from scripts.canary import main

def test_canary_symbols_flag_evaluates_only_requested_symbol(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD","1000")
    journal=tmp_path/"canary-symbols.sqlite3"
    rc=main(["--symbols","BTCUSDT","--journal",str(journal)])
    output=capsys.readouterr().out
    assert rc==0
    evaluated=[line for line in output.splitlines() if line.startswith("EVALUATED:")]
    assert evaluated==["EVALUATED: BTCUSDT"]
    assert journal.is_file()

def test_canary_daily_signal_cap_is_respected_and_journal_is_separate(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    import scripts.canary as canary
    from scripts._offline_pipeline import PipelineRun
    monkeypatch.setenv("ASSUMED_ACCOUNT_EQUITY_USD","1000")
    journal=tmp_path/"canary-cap.sqlite3"
    signals=[SimpleNamespace(signal_id=f"signal-{i}",created_ts_ms=i,grade="B",strategy_source="S1",symbol="BTCUSDT") for i in range(3)]
    monkeypatch.setattr(canary,"run_offline_pipeline",lambda cfg,symbol,equity:PipelineRun(symbol,True,3,0,signals,signals,[]))
    cfg=load_all(); production=cfg.config_dir.parent/cfg.system["database_paths"]["sqlite_path"]
    production_before=production.read_bytes() if production.exists() else None
    rc=main(["--max-daily-signals","1","--journal",str(journal)])
    output=capsys.readouterr().out
    assert rc==0
    emitted=int(next(line.split(":",1)[1] for line in output.splitlines() if line.startswith("signals_emitted:")))
    assert emitted==1
    assert journal.resolve()!=production.resolve()
    assert journal.is_file()
    with sqlite3.connect(journal) as conn:
        signal_rows=conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        performance=conn.execute("SELECT payload_json FROM performance").fetchall()
    assert signal_rows==1
    assert any(json.loads(row[0]).get("kind")=="canary_summary" for row in performance)
    if production_before is None:
        assert not production.exists(),"canary must not create the production journal"
    else:
        assert production.exists() and production.read_bytes()==production_before, "canary must not modify the production journal"
