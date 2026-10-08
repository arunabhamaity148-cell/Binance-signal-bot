"""Non-blocking journal submission with a dedicated SQLite/JSONL writer thread."""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

JOURNAL_KINDS=frozenset({"signals","vetoes","news","errors","performance"})
_STOP=object()

@dataclass(frozen=True)
class _JournalItem:
    kind: str
    record_id: str
    created_ts_ms: int
    payload_json: str

class JournalWriter:
    """`write` only serializes and enqueues; all filesystem/SQLite I/O is on a worker.

    Duplicate signal IDs (and duplicate record IDs in every journal) are
    ignored by SQLite and are not mirrored twice. JSONL files rotate at
    `max_bytes`, retaining `backup_count` old segments per journal type.
    """
    def __init__(self, db_path: str | Path, *, mirror_dir: str | Path | None=None,
                 queue_size: int=2048, max_bytes: int=2_000_000, backup_count: int=3) -> None:
        self.db_path=Path(db_path)
        self.mirror_dir=Path(mirror_dir) if mirror_dir is not None else self.db_path.with_suffix("").with_name(self.db_path.stem+"_jsonl")
        self.max_bytes=max(1024,int(max_bytes)); self.backup_count=max(0,int(backup_count))
        self._queue: queue.Queue = queue.Queue(maxsize=max(1,int(queue_size)))
        self._ready=threading.Event(); self._closed=False; self._lock=threading.Lock()
        self._last_error: str | None=None; self._written=0; self._dropped=0
        self._thread=threading.Thread(target=self._worker,name="monitoring-journal-writer",daemon=True)
        self._thread.start()

    @staticmethod
    def _to_payload(value) -> dict:
        if hasattr(value,"model_dump"):
            value=value.model_dump(mode="json")
        elif hasattr(value,"dict") and callable(value.dict):
            value=value.dict()
        elif hasattr(value,"__dataclass_fields__"):
            from dataclasses import asdict
            value=asdict(value)
        if isinstance(value,dict): return value
        return {"value":value}

    def write(self, kind: str, payload, *, record_id: str | None=None, created_ts_ms: int | None=None) -> bool:
        if kind not in JOURNAL_KINDS: raise ValueError(f"unknown journal kind: {kind}")
        with self._lock:
            if self._closed: return False
        try:
            body=self._to_payload(payload)
            key=record_id or body.get("signal_id") or body.get("event_id") or body.get("record_id") or uuid.uuid4().hex
            item=_JournalItem(kind,str(key),int(created_ts_ms if created_ts_ms is not None else time.time()*1000),json.dumps(body,sort_keys=True,separators=(",",":"),default=str))
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            with self._lock: self._dropped+=1; self._last_error="journal queue full"
            return False
        except Exception as exc:
            with self._lock: self._dropped+=1; self._last_error=f"enqueue serialization: {type(exc).__name__}: {exc}"
            return False

    def _prepare(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True,exist_ok=True); self.mirror_dir.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(self.db_path,timeout=5)
        for kind in JOURNAL_KINDS:
            key="signal_id" if kind=="signals" else "record_id"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {kind} ({key} TEXT PRIMARY KEY, created_ts_ms INTEGER NOT NULL, payload_json TEXT NOT NULL)")
        conn.commit(); return conn

    def _append_mirror(self,item: _JournalItem) -> None:
        path=self.mirror_dir/f"{item.kind}.jsonl"
        encoded=(json.dumps({"record_id":item.record_id,"created_ts_ms":item.created_ts_ms,"payload":json.loads(item.payload_json)},sort_keys=True,default=str)+"\n").encode("utf-8")
        try: size=path.stat().st_size
        except FileNotFoundError: size=0
        if size+len(encoded)>self.max_bytes and size:
            for index in range(self.backup_count,0,-1):
                src=path.with_name(path.name+f".{index}")
                dest=path.with_name(path.name+f".{index+1}")
                if index==self.backup_count and src.exists(): src.unlink()
                elif src.exists(): os.replace(src,dest)
            if path.exists(): os.replace(path,path.with_name(path.name+".1"))
        with path.open("ab") as fh: fh.write(encoded)

    def _worker(self) -> None:
        conn=None
        try:
            conn=self._prepare()
        except Exception as exc:
            with self._lock: self._last_error=f"journal initialization: {type(exc).__name__}: {exc}"
        finally: self._ready.set()
        while True:
            item=self._queue.get()
            try:
                if item is _STOP: break
                if conn is None:
                    with self._lock: self._dropped+=1
                    continue
                key="signal_id" if item.kind=="signals" else "record_id"
                cur=conn.execute(f"INSERT OR IGNORE INTO {item.kind} ({key},created_ts_ms,payload_json) VALUES (?,?,?)",(item.record_id,item.created_ts_ms,item.payload_json))
                conn.commit()
                if cur.rowcount:
                    self._append_mirror(item)
                    with self._lock: self._written+=1
            except Exception as exc:
                if conn is not None:
                    try: conn.rollback()
                    except Exception: pass
                with self._lock: self._dropped+=1; self._last_error=f"journal write: {type(exc).__name__}: {exc}"
            finally: self._queue.task_done()
        if conn is not None: conn.close()

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        with self._lock:
            if self._closed: return
            self._closed=True
        self._ready.wait()
        self._queue.put(_STOP)
        self._thread.join()

    @property
    def status(self) -> dict:
        with self._lock:
            return {"ready":self._ready.is_set(),"closed":self._closed,"queue_depth":self._queue.qsize(),
                    "written":self._written,"dropped":self._dropped,"last_error":self._last_error}

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
