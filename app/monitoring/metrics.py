"""Thread-safe in-memory runtime counters and gauges (not backtest metrics)."""
from __future__ import annotations

from copy import deepcopy
from threading import Lock


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock=Lock()
        self._counters={"signals_emitted":0,"vetoes_blocked":0,"signals_per_grade":{},
                        "signals_per_strategy":{},"signals_per_symbol":{}}
        self._gauges={"current_feed_lag_ms":None,"current_ws_reconnect_count":None,"active_signal_count":0}

    @staticmethod
    def _inc(mapping: dict, key: str, amount: int=1) -> None:
        mapping[key]=mapping.get(key,0)+amount

    def record_signal(self, *, grade: str, strategy: str, symbol: str) -> None:
        with self._lock:
            self._counters["signals_emitted"]+=1
            self._inc(self._counters["signals_per_grade"],str(grade))
            self._inc(self._counters["signals_per_strategy"],str(strategy))
            self._inc(self._counters["signals_per_symbol"],str(symbol))

    def record_veto(self, *, blocked: bool=True, amount: int=1) -> None:
        if not blocked: return
        with self._lock: self._counters["vetoes_blocked"]+=max(0,int(amount))

    def set_feed_lag(self, value: int | None) -> None:
        with self._lock: self._gauges["current_feed_lag_ms"]=None if value is None else max(0,int(value))

    def set_ws_reconnect_count(self, value: int | None) -> None:
        with self._lock: self._gauges["current_ws_reconnect_count"]=None if value is None else max(0,int(value))

    def set_active_signal_count(self, value: int) -> None:
        with self._lock: self._gauges["active_signal_count"]=max(0,int(value))

    def snapshot(self) -> dict:
        with self._lock:
            return {"counters":deepcopy(self._counters),"gauges":deepcopy(self._gauges)}
