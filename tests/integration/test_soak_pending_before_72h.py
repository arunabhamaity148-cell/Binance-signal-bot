from datetime import datetime, timedelta, timezone
from app.monitoring.soak import REQUIRED_SOAK_SECONDS, SoakMonitor

def test_soak_is_pending_before_72_hours():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    monitor=SoakMonitor(started_at=start)
    assert monitor.status(now=start+timedelta(seconds=REQUIRED_SOAK_SECONDS-1))=="PENDING"

def test_soak_completes_after_72_hours():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    monitor=SoakMonitor(started_at=start)
    assert monitor.status(now=start+timedelta(seconds=REQUIRED_SOAK_SECONDS+1))=="COMPLETED"

def test_soak_status_at_exact_72_hour_boundary():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    monitor=SoakMonitor(started_at=start)
    assert monitor.status(now=start+timedelta(seconds=REQUIRED_SOAK_SECONDS))=="COMPLETED"

def test_explicit_test_run_cannot_be_promoted_by_advanced_clock():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    monitor=SoakMonitor(started_at=start,test_run=True)
    report=monitor.report(now=start+timedelta(days=10))
    assert report["status"]=="PENDING"
    assert report["run_kind"]=="TEST RUN"
