#!/usr/bin/env python3
"""Limited-scope paper canary using synthetic offline inputs and a separate journal."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.config import get_assumed_account_equity_usd,load_and_validate_all
from app.monitoring.journals import JournalWriter
from app.monitoring.metrics import RuntimeMetrics
from scripts._offline_pipeline import run_offline_pipeline

def _resolve(root: Path,value: str) -> Path:
    path=Path(value)
    return path if path.is_absolute() else root/path

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Paper canary: exactly one symbol scope or daily signal cap; no live delivery.")
    scope=parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbols",help="comma-separated configured symbols")
    scope.add_argument("--max-daily-signals",type=int,help="upper bound on eligible paper signals")
    parser.add_argument("--journal",default="runtime/canary_journal.sqlite3")
    args=parser.parse_args(argv)
    if args.max_daily_signals is not None and args.max_daily_signals<1:
        sys.stderr.write("--max-daily-signals must be >= 1\n"); return 1
    try:
        cfg=load_and_validate_all(); root=cfg.config_dir.parent
        journal_path=_resolve(root,args.journal)
        prod_path=_resolve(root,cfg.system["database_paths"]["sqlite_path"])
        if journal_path.resolve()==prod_path.resolve(): raise ValueError("canary journal must be separate from production journal")
        enabled=cfg.enabled_symbols()
        if args.symbols is not None:
            symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if not symbols or len(set(symbols))!=len(symbols): raise ValueError("--symbols must contain unique configured symbols")
            unknown=[s for s in symbols if s not in enabled]
            if unknown: raise ValueError(f"symbols are not enabled in configuration: {unknown}")
        else: symbols=enabled
        equity=get_assumed_account_equity_usd(); metrics=RuntimeMetrics(); emitted=0; evaluated=[]; failures=[]
        with JournalWriter(journal_path) as journal:
            for symbol in symbols:
                if args.max_daily_signals is not None and emitted>=args.max_daily_signals: break
                result=run_offline_pipeline(cfg,symbol,equity); evaluated.append(symbol)
                journal.write("performance",{"kind":"canary_evaluation","symbol":symbol,"candidate_count":result.candidates,
                    "vetoes_blocked":result.vetoes_blocked,"errors":result.errors})
                for signal in result.eligible_signals:
                    if args.max_daily_signals is not None and emitted>=args.max_daily_signals: break
                    journal.write("signals",signal,record_id=signal.signal_id,created_ts_ms=signal.created_ts_ms)
                    metrics.record_signal(grade=signal.grade,strategy=signal.strategy_source,symbol=signal.symbol)
                    emitted+=1
                failures.extend(result.errors)
            summary={"kind":"canary_summary","evaluated_symbols":evaluated,"signals_emitted":emitted,
                     "scope":"symbols" if args.symbols is not None else "max_daily_signals",
                     "daily_cap":args.max_daily_signals}
            journal.write("performance",summary); journal.flush()
            status=journal.status
            if status["dropped"] or status["last_error"]: raise RuntimeError(f"canary journal failed: {status}")
        sys.stdout.write("CANARY PAPER RUN\nsource: synthetic offline fixture\n"+"".join(f"EVALUATED: {symbol}\n" for symbol in evaluated))
        sys.stdout.write(f"signals_emitted: {emitted}\njournal: {journal_path}\nexternal delivery: disabled\n")
        if failures: return 1
        return 0
    except Exception as exc:
        sys.stderr.write(f"CANARY: FAIL ({type(exc).__name__}: {exc})\n"); return 1

if __name__=="__main__": raise SystemExit(main())
