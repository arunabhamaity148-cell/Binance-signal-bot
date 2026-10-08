#!/usr/bin/env python3
"""Paper-mode runtime observer. A real run is complete only after 72 wall-clock hours."""
from __future__ import annotations
import argparse
import math
import resource
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.config import load_all
from app.monitoring.journals import JournalWriter
from app.monitoring.soak import SoakMonitor

def _resolve(root: Path,value: str) -> Path:
    path=Path(value)
    return path if path.is_absolute() else root/path

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Observe paper-mode process health; no orders or network required.")
    parser.add_argument("--hours",type=float,default=72.0)
    parser.add_argument("--journal",default="runtime/soak_journal.sqlite3")
    parser.add_argument("--sample-interval-seconds",type=float,default=60.0)
    args=parser.parse_args(argv)
    if not math.isfinite(args.hours) or args.hours<=0 or not math.isfinite(args.sample_interval_seconds) or args.sample_interval_seconds<=0:
        sys.stderr.write("hours and sample interval must be finite and positive\n"); return 1
    try:
        cfg=load_all(); root=cfg.config_dir.parent
        journal_path=_resolve(root,args.journal)
        production=cfg.system["database_paths"]["sqlite_path"]
        prod_path=_resolve(root,production)
        if journal_path.resolve()==prod_path.resolve(): raise ValueError("soak journal must be separate from production journal")
        test_run=args.hours<72.0
        monitor=SoakMonitor(test_run=test_run)
        deadline=time.monotonic()+args.hours*3600
        start=time.monotonic()
        with JournalWriter(journal_path) as journal:
            initial_events = (
                monitor.record_feed_stability(status="unknown",reason="no live feed state supplied to paper observer"),
                monitor.record_stale_data(status="unknown",reason="no live candle timestamps supplied"),
                monitor.record_signal_why_latency(status="unknown",reason="no signal delivery stream supplied"),
                monitor.record_veto_rate(status="unknown",reason="no evaluation stream supplied"),
                monitor.record_duplicate(status="unknown",reason="no signal-id stream supplied"),
                monitor.record_restart_recovery(status="unknown",reason="observer startup; no prior process state supplied"),
            )
            for event in initial_events:
                journal.write("performance",{"kind":event.kind,"timestamp":event.timestamp.isoformat(),**event.details})
            while True:
                try:
                    usage=resource.getrusage(resource.RUSAGE_SELF)
                    rss_bytes=int(usage.ru_maxrss*(1024 if sys.platform!="darwin" else 1))
                    monitor.record_cpu_rss(cpu_seconds=float(usage.ru_utime+usage.ru_stime),rss_bytes=rss_bytes)
                    journal.write("performance",{"kind":"cpu_rss","cpu_seconds":usage.ru_utime+usage.ru_stime,"rss_bytes":rss_bytes})
                except Exception as exc:
                    monitor.record_exception(source="resource_sampler",error=type(exc).__name__)
                    journal.write("errors",{"source":"resource_sampler","error":type(exc).__name__})
                remaining=deadline-time.monotonic()
                if remaining<=0: break
                time.sleep(min(args.sample_interval_seconds,remaining))
            report=monitor.report()
            journal.write("performance",{"kind":"soak_report",**report})
            journal.flush()
            state=journal.status
            if state["last_error"] or state["dropped"]: raise RuntimeError(f"soak journal write failed: {state}")
        label="PARTIAL REPORT" if report["status"]=="PENDING" else "SOAK REPORT"
        sys.stdout.write(f"{label}\nrun_kind: {report['run_kind']}\nstatus: {report['status']}\nelapsed_hours: {report['elapsed_seconds']/3600:.4f}\nrequired_hours: 72\nevents: {report['event_count']}\njournal: {journal_path}\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"SOAK TEST: FAIL ({type(exc).__name__}: {exc})\n"); return 1

if __name__=="__main__": raise SystemExit(main())
