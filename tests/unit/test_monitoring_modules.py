from __future__ import annotations
import json
import sqlite3
import threading
from app.core.models import FeedHealth
from app.monitoring.health import build_health_report
from app.monitoring.journals import JournalWriter
from app.monitoring.metrics import RuntimeMetrics

def test_health_report_aggregates_feed_news_queue_and_database():
    report=build_health_report(feed_health={"btc":FeedHealth("BTCUSDT","btcusdt@aggTrade",99_000,1,True)},
        news_source_health={"wire":True},telegram_queue_depth=2,database_check=True,now_ts_ms=100_000)
    assert report.overall=="healthy"
    assert report.feeds_by_symbol["BTCUSDT"].details["streams"][0]["lag_ms"]==1_000
    assert report.telegram_queue_depth==2 and report.database_status=="healthy"

def test_health_report_never_raises_and_degrades_missing_or_broken_inputs_to_unknown():
    class Broken:
        def items(self): raise RuntimeError("unavailable")
    report=build_health_report(feed_health=Broken(),database_check=lambda:(_ for _ in ()).throw(OSError("offline")))
    assert report.overall=="unknown"
    assert report.database_status=="unknown"
    assert report.errors

def test_runtime_metrics_are_thread_safe_and_return_independent_snapshots():
    metrics=RuntimeMetrics()
    def update():
        for _ in range(100): metrics.record_signal(grade="B",strategy="S1",symbol="BTCUSDT")
    threads=[threading.Thread(target=update) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    metrics.record_veto(blocked=True); metrics.set_feed_lag(15); metrics.set_ws_reconnect_count(2); metrics.set_active_signal_count(3)
    snap=metrics.snapshot()
    assert snap["counters"]["signals_emitted"]==800
    assert snap["counters"]["signals_per_grade"]=={"B":800}
    assert snap["counters"]["signals_per_strategy"]=={"S1":800}
    assert snap["counters"]["signals_per_symbol"]=={"BTCUSDT":800}
    assert snap["counters"]["vetoes_blocked"]==1
    snap["counters"]["signals_emitted"]=-1
    assert metrics.snapshot()["counters"]["signals_emitted"]==800

def test_journal_writer_is_nonblocking_idempotent_and_mirrors_each_type(tmp_path):
    db=tmp_path/"journals.sqlite3"; mirrors=tmp_path/"jsonl"
    writer=JournalWriter(db,mirror_dir=mirrors,max_bytes=4096)
    payload={"signal_id":"sig-1","symbol":"BTCUSDT","grade":"B"}
    assert writer.write("signals",payload)
    assert writer.write("signals",payload)
    assert writer.write("vetoes",{"event_id":"veto-1","state":"BLOCK"},record_id="veto-1")
    assert writer.write("news",{"event_id":"news-1"},record_id="news-1")
    assert writer.write("errors",{"event_id":"err-1"},record_id="err-1")
    assert writer.write("performance",{"event_id":"perf-1"},record_id="perf-1")
    writer.flush(); state=writer.status; writer.close()
    assert state["written"]==5 and state["dropped"]==0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals WHERE signal_id='sig-1'").fetchone()[0]==1
    assert len((mirrors/"signals.jsonl").read_text().splitlines())==1
    assert all((mirrors/f"{kind}.jsonl").is_file() for kind in ("signals","vetoes","news","errors","performance"))
    mirrored=json.loads((mirrors/"signals.jsonl").read_text().splitlines()[0])
    assert mirrored["record_id"]=="sig-1"

def test_journal_jsonl_mirror_rotates(tmp_path):
    mirrors=tmp_path/"rotating"
    writer=JournalWriter(tmp_path/"rotate.sqlite3",mirror_dir=mirrors,max_bytes=1024,backup_count=2)
    for index in range(5):
        assert writer.write("performance",{"record_id":f"large-{index}","blob":"x"*700},record_id=f"large-{index}")
    writer.flush(); writer.close()
    assert (mirrors/"performance.jsonl").is_file()
    assert (mirrors/"performance.jsonl.1").is_file()
