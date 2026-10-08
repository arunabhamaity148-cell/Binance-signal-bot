#!/usr/bin/env python3
"""Read-only configuration, Telegram, SQLite journal, and runtime health summary."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.config import load_all
from app.monitoring.health import build_health_report
from scripts.validate_config import run_validation

def _resolve(root: Path,value: str) -> Path:
    path=Path(value)
    return path if path.is_absolute() else root/path

def _read_db(path: Path) -> tuple[bool,int|None,str|None]:
    if not path.is_file(): return False,None,"journal file is unavailable"
    try:
        uri=f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri,uri=True,timeout=1) as conn:
            rows=conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return True,len(rows),None
    except Exception as exc: return False,None,f"{type(exc).__name__}: {exc}"

def main() -> int:
    try: cfg=load_all()
    except Exception as exc:
        sys.stdout.write(f"HEALTH: FAILED\nconfig: unavailable ({type(exc).__name__}: {exc})\n")
        return 1
    errors=run_validation(cfg)
    telegram=cfg.system.get("telegram_limits",{})
    telegram_ok=all(k in telegram for k in ("messages_per_sec_per_chat","messages_per_min_per_group","max_message_chars"))
    telegram_ok=telegram_ok and all(float(telegram[k])>0 for k in telegram)
    root=cfg.config_dir.parent
    db_path=_resolve(root,cfg.system.get("database_paths",{}).get("sqlite_path",""))
    db_ok,table_count,db_error=_read_db(db_path)
    runtime_journals=sorted((root/"runtime").glob("*_journal.sqlite3")) if (root/"runtime").is_dir() else []
    read_runtime=[]
    for path in runtime_journals:
        ok,count,error=_read_db(path); read_runtime.append({"path":str(path),"available":ok,"tables":count,"error":error})
    report=build_health_report(feed_health=None,news_source_health=None,telegram_queue_depth=None,database_check=db_ok)
    config_status="healthy" if not errors else "unhealthy"
    telegram_status="healthy" if telegram_ok else "unhealthy"
    sys.stdout.write("HEALTH SUMMARY\n")
    sys.stdout.write(f"config_validation: {config_status}"+(f" ({len(errors)} errors)" if errors else "")+"\n")
    sys.stdout.write(f"telegram_config: {telegram_status}\n")
    sys.stdout.write(f"production_journal: {'healthy' if db_ok else 'unavailable'} ({db_path}; tables={table_count}; {db_error or 'read-only check passed'})\n")
    sys.stdout.write(f"runtime_journals_read: {len(read_runtime)}\n")
    for item in read_runtime: sys.stdout.write(f"  {item['path']}: {'healthy' if item['available'] else 'unavailable'} tables={item['tables']}\n")
    sys.stdout.write(f"live_runtime_state: {report.overall.upper()} (feeds/news/queue were not supplied to this standalone check)\n")
    if errors: return 1
    if not telegram_ok: return 1
    if not db_ok: return 3
    if report.overall!="healthy": return 1
    return 0

if __name__=="__main__": raise SystemExit(main())
