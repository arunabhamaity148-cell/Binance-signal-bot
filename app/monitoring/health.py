"""Best-effort runtime health snapshot. Collection failures become `unknown`, never exceptions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Mapping


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


@dataclass(frozen=True)
class ComponentHealth:
    status: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class HealthReport:
    generated_ts_ms: int
    overall: str
    feeds_by_symbol: dict[str, ComponentHealth]
    news_sources: dict[str, ComponentHealth]
    telegram_queue_depth: int | None
    telegram_status: str
    database_status: str
    database_details: dict = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def build_health_report(
    *, feed_health: Mapping | None = None,
    news_source_health: Mapping | None = None,
    telegram_queue_depth=None,
    database_check: bool | Callable[[], bool] | None = None,
    now_ts_ms: int | None = None,
    feed_stale_after_ms: int = 5_000,
) -> HealthReport:
    """Build an aggregate report; malformed or failing sources are `unknown`.

    Inputs may be mappings or objects with the usual FeedHealth fields. The
    function is deliberately fail-soft: even unexpected conversion/callback
    errors are represented in the returned report.
    """
    errors: list[str] = []
    try:
        import time
        now = int(now_ts_ms if now_ts_ms is not None else time.time() * 1000)
        feeds: dict[str, ComponentHealth] = {}
        grouped: dict[str, list] = {}
        if not feed_health:
            feeds["all_symbols"] = ComponentHealth("unknown", {"reason":"feed state not supplied"})
        for key, state in (feed_health or {}).items():
            symbol = _value(state, "symbol") or str(key).split("@", 1)[0].upper()
            grouped.setdefault(str(symbol), []).append((str(key), state))
        for symbol, states in grouped.items():
            statuses=[]; details=[]
            for stream, state in states:
                connected=_value(state,"is_connected")
                received=_value(state,"last_message_received_ts_ms")
                reconnects=_value(state,"reconnect_count_window")
                try: lag=now-int(received) if received is not None else None
                except Exception: lag=None
                status="unknown" if connected is None or lag is None else ("healthy" if bool(connected) and lag <= feed_stale_after_ms else "unhealthy")
                statuses.append(status)
                details.append({"stream":stream,"connected":connected,"lag_ms":lag,"reconnect_count":reconnects})
            aggregate="unhealthy" if "unhealthy" in statuses else "unknown" if "unknown" in statuses else "healthy"
            feeds[symbol]=ComponentHealth(aggregate,{"streams":details})
        news: dict[str, ComponentHealth] = {}
        if not news_source_health:
            news["all_sources"]=ComponentHealth("unknown",{"reason":"source health not supplied"})
        else:
            for source,state in news_source_health.items():
                value=_value(state,"healthy",_value(state,"is_healthy",state if isinstance(state,bool) else None))
                status="unknown" if value is None else "healthy" if bool(value) else "unhealthy"
                news[str(source)]=ComponentHealth(status, {"source_state":str(state)[:200]})
        try:
            depth=None if telegram_queue_depth is None else int(telegram_queue_depth)
            tg_status="unknown" if depth is None else "healthy" if depth >= 0 else "unhealthy"
            if depth is not None and depth < 0: depth=None
        except Exception as exc:
            depth=None; tg_status="unknown"; errors.append(f"telegram queue: {type(exc).__name__}")
        try:
            db=database_check() if callable(database_check) else database_check
            db_status="unknown" if db is None else "healthy" if bool(db) else "unhealthy"
        except Exception as exc:
            db_status="unknown"; errors.append(f"database check: {type(exc).__name__}")
        components=[c.status for c in feeds.values()]+[c.status for c in news.values()]+[tg_status,db_status]
        overall="unhealthy" if "unhealthy" in components else "unknown" if "unknown" in components else "healthy"
        return HealthReport(now,overall,feeds,news,depth,tg_status,db_status,{},tuple(errors))
    except Exception as exc:  # absolute no-raise contract, including malformed mappings
        try:
            import time
            now=int(now_ts_ms if now_ts_ms is not None else time.time()*1000)
        except Exception:
            now=0
        return HealthReport(now,"unknown",{}, {"all_sources":ComponentHealth("unknown")},None,"unknown","unknown",{},(f"collector failure: {type(exc).__name__}",))
