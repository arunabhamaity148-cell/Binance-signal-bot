#!/usr/bin/env python3
"""Offline end-to-end smoke test; synthetic signal is written only to a smoke journal."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.config import get_assumed_account_equity_usd,load_and_validate_all
from app.monitoring.journals import JournalWriter
from scripts._offline_pipeline import run_offline_pipeline

def _configured_path(cfg,raw: str) -> Path:
    path=Path(raw)
    return path if path.is_absolute() else cfg.config_dir.parent/path

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Offline synthetic data-to-journal smoke check; no network access.")
    parser.add_argument("--symbol",default="BTCUSDT")
    parser.add_argument("--journal",default="runtime/smoke_journal.sqlite3")
    args=parser.parse_args(argv)
    try:
        cfg=load_and_validate_all()
        journal_path=_configured_path(cfg,args.journal)
        production_path=_configured_path(cfg,cfg.system["database_paths"]["sqlite_path"])
        if journal_path.resolve()==production_path.resolve(): raise ValueError("smoke journal must be separate from production journal")
        equity=get_assumed_account_equity_usd()
        result=run_offline_pipeline(cfg,args.symbol,equity)
        if result.errors: raise RuntimeError("pipeline errors: "+"; ".join(result.errors))
        if not result.built_signals: raise RuntimeError("offline fixture did not reach signal construction")
        signal=result.built_signals[0]
        with JournalWriter(journal_path) as journal:
            journal.write("performance",{"stage":"data","source":"synthetic_offline_fixture","symbol":args.symbol})
            journal.write("performance",{"stage":"strategies","candidate_count":result.candidates,"symbol":args.symbol})
            journal.write("performance",{"stage":"consensus_veto","blocked":result.vetoes_blocked,"grade":signal.grade})
            journal.write("signals",signal,record_id=signal.signal_id,created_ts_ms=signal.created_ts_ms)
            journal.write("performance",{"stage":"signal_persisted","signal_id":signal.signal_id,"rr_gate_pass":signal.rr_tp2>=cfg.risk["min_rr_tp2"]})
            journal.flush()
            state=journal.status
            if state["dropped"] or state["last_error"]: raise RuntimeError(f"smoke journal failed: {state}")
        sys.stdout.write(f"SMOKE TEST: PASS\nsource: synthetic offline fixture\nsymbol: {args.symbol}\ncandidates: {result.candidates}\nsignals constructed and persisted: 1\njournal: {journal_path}\nexternal network calls: none\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"SMOKE TEST: FAIL ({type(exc).__name__}: {exc})\n")
        return 1

if __name__=="__main__": raise SystemExit(main())
