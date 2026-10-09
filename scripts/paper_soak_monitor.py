#!/usr/bin/env python3
"""Read-only 72-hour observer for the live signal-only paper bot.

The monitor never calls Binance/Telegram and never changes bot state. It tails
an existing bot log, emits one JSON metric snapshot per interval, and writes a
final summary after the required wall-clock duration.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_SECONDS = 72 * 60 * 60
EXPECTED_ACTIVE = {
    "g1_data_integrity", "g2_feed_health", "g4_spread_explosion",
    "g5_oi_anomaly", "g6_funding_extreme", "g8_volatility_flash",
    "g9_btc_regime",
}
EVENT_MARKERS = (
    "loop_health", "diag_strategy", "diag_veto", "s3_data_check",
    "candidate_created", "signal_created", "signal_published",
    "news_source_failed", "ERROR", "CRITICAL", "Traceback",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_payload(line: str) -> dict[str, Any]:
    """Parse either JSON diagnostics or logging ``{'context': ...}`` payloads."""
    if " | " not in line:
        return {}
    raw = line.split(" | ", 1)[1].strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(raw)
            return value if isinstance(value, dict) else {}
        except (SyntaxError, ValueError):
            return {}


def active_vetoes(config_path: Path) -> list[str]:
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(config_path.read_text()) or {}
        return sorted(
            key for key, value in cfg.items()
            if isinstance(value, dict) and value.get("enabled", True)
        )
    except Exception:
        return []


def bot_pids() -> list[int]:
    found: list[int] = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
            if command == "python -m app.main":
                found.append(int(entry.name))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return sorted(found)


def new_counts() -> Counter[str]:
    return Counter({
        "lines": 0, "loop_health": 0, "strategy_eval": 0,
        "strategy_rejected": 0,
        "candidate_created": 0, "signal_created": 0,
        "signal_published": 0, "diag_strategy": 0, "diag_veto": 0,
        "s3_data_check": 0, "s3_ls_ratio_stale": 0,
        "news_source_failed": 0, "news_timeout": 0,
        "errors": 0, "critical": 0, "tracebacks": 0,
        "ws_disconnected": 0, "stale_or_unhealthy": 0,
    })


def scan_lines(lines: list[str], counts: Counter[str], strategy_reasons: Counter[str], vetoes: Counter[str], news_sources: Counter[str]) -> dict[str, Any]:
    latest_loop: dict[str, Any] = {}
    latest_s3: dict[str, Any] = {}
    for line in lines:
        counts["lines"] += 1
        if "loop_health" in line:
            counts["loop_health"] += 1
            latest_loop = parse_payload(line).get("context", parse_payload(line))
        if "strategy_eval" in line:
            counts["strategy_eval"] += 1
        for marker in ("candidate_created", "signal_created", "signal_published"):
            if marker in line:
                counts[marker] += 1
        if "diag_strategy" in line:
            counts["diag_strategy"] += 1
            payload = parse_payload(line)
            strategy_reasons[str(payload.get("reason", "unknown"))] += 1
            if payload.get("decision") == "rejected":
                counts["strategy_rejected"] += 1
        if "diag_veto" in line:
            counts["diag_veto"] += 1
            payload = parse_payload(line)
            guard = str(payload.get("guard", "unknown"))
            reason = str(payload.get("reason", "PASS"))
            vetoes[f"{guard}:{reason}"] += 1
        if "s3_data_check" in line:
            counts["s3_data_check"] += 1
            payload = parse_payload(line).get("context", parse_payload(line))
            latest_s3 = payload
            if payload.get("reason") == "ls_ratio_stale":
                counts["s3_ls_ratio_stale"] += 1
        if "news_source_failed" in line:
            counts["news_source_failed"] += 1
            payload = parse_payload(line).get("context", parse_payload(line))
            news_sources[str(payload.get("source", "unknown"))] += 1
            if "timeout" in line.lower() or "timed out" in line.lower():
                counts["news_timeout"] += 1
        if "CRITICAL" in line:
            counts["critical"] += 1
        if "Traceback" in line:
            counts["tracebacks"] += 1
        if re.search(r"\bERROR\b", line):
            counts["errors"] += 1
        if re.search(r"disconnect|unhealthy|stale", line, re.IGNORECASE):
            counts["stale_or_unhealthy"] += 1
        if "ws_connected': False" in line or '"ws_connected": false' in line.lower():
            counts["ws_disconnected"] += 1
    return {"latest_loop": latest_loop, "latest_s3": latest_s3}


def load_state(path: Path, new_run: bool) -> dict[str, Any]:
    if path.exists() and not new_run:
        try:
            state = json.loads(path.read_text())
            if isinstance(state, dict) and state.get("started_at"):
                return state
        except (OSError, json.JSONDecodeError):
            pass
    return {"started_at": now_iso(), "log_offset": 0, "last_log_size": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only live 72-hour paper-soak monitor")
    parser.add_argument("--log", default="/home/ubuntu/binance_bot.log")
    parser.add_argument("--output-dir", default="runtime/paper_soak")
    parser.add_argument("--config", default="config/veto.yaml")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--hours", type=float, default=72.0)
    parser.add_argument("--zero-signal-alert-minutes", type=float, default=60.0)
    parser.add_argument("--new-run", action="store_true")
    args = parser.parse_args(argv)
    if args.hours <= 0 or args.interval_seconds <= 0 or args.zero_signal_alert_minutes <= 0:
        parser.error("hours, interval, and zero-signal alert duration must be positive")

    log_path = Path(args.log).expanduser()
    output = Path(args.output_dir).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    metrics_path = output / "metrics.jsonl"
    summary_path = output / "summary.json"
    state = load_state(state_path, args.new_run)
    started = datetime.fromisoformat(state["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    deadline = started.timestamp() + args.hours * 3600
    counts = new_counts()
    strategy_reasons: Counter[str] = Counter()
    vetoes: Counter[str] = Counter()
    news_sources: Counter[str] = Counter()
    last_candidate_at = state.get("last_candidate_at") or started.timestamp()
    last_rejection_at = state.get("last_rejection_at")
    last_stale_at = state.get("last_stale_at")
    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    sys.stdout.write(f"PAPER SOAK MONITOR STARTED: {now_iso()}\n")
    sys.stdout.write(f"started_at: {started.isoformat()}\nrequired_hours: {args.hours}\nlog: {log_path}\noutput: {output}\n")
    sys.stdout.flush()

    while not stop and time.time() < deadline:
        interval_counts = new_counts()
        context: dict[str, Any] = {}
        try:
            size = log_path.stat().st_size
            offset = int(state.get("log_offset", 0))
            if size < offset:
                offset = 0
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                lines = handle.readlines()
                state["log_offset"] = handle.tell()
            context = scan_lines(lines, interval_counts, strategy_reasons, vetoes, news_sources)
            counts.update(interval_counts)
            if interval_counts["candidate_created"] or interval_counts["signal_created"]:
                last_candidate_at = time.time()
                state["last_candidate_at"] = last_candidate_at
            if interval_counts["strategy_rejected"]:
                last_rejection_at = time.time()
                state["last_rejection_at"] = last_rejection_at
            if interval_counts["stale_or_unhealthy"]:
                last_stale_at = time.time()
                state["last_stale_at"] = last_stale_at
        except FileNotFoundError:
            context["log_missing"] = True
        except OSError as exc:
            context["log_error"] = f"{type(exc).__name__}: {exc}"

        loop = context.get("latest_loop", {}) or state.get("latest_loop", {})
        if context.get("latest_loop"):
            state["latest_loop"] = context["latest_loop"]
        pids = bot_pids()
        active = active_vetoes(Path(args.config))
        zero_signal_seconds = max(0, time.time() - last_candidate_at)
        alerts: list[str] = []
        if not pids:
            alerts.append("BOT_PROCESS_MISSING")
        if context.get("log_missing"):
            alerts.append("LOG_MISSING")
        if loop.get("ws_connected") is False:
            alerts.append("WEBSOCKET_DISCONNECTED")
        if loop.get("snapshots_ready") is not None and loop.get("snapshot_total") is not None:
            if loop["snapshots_ready"] < loop["snapshot_total"]:
                alerts.append("SNAPSHOTS_NOT_READY")
        loop_zero = loop.get("candidates_last_5min") == 0 and loop.get("signals_last_5min") == 0
        if loop_zero and zero_signal_seconds >= args.zero_signal_alert_minutes * 60:
            alerts.append("STARVATION_WARNING_ALL_STRATEGIES_REJECTED" if last_rejection_at else "ZERO_CANDIDATE_WINDOW")
        if loop_zero and zero_signal_seconds >= max(2 * 60 * 60, 2 * args.zero_signal_alert_minutes * 60):
            if last_stale_at and last_stale_at >= (last_candidate_at or 0):
                alerts.append("STARVATION_CRITICAL_STALE_FEED")
            elif last_rejection_at:
                alerts.append("STARVATION_CRITICAL_ALL_STRATEGIES_REJECTED")
        if active and set(active) != EXPECTED_ACTIVE:
            alerts.append("ACTIVE_VETO_SET_MISMATCH")

        metric = {
            "timestamp": now_iso(), "pids": pids, "active_vetoes": active,
            "interval": dict(interval_counts), "cumulative": dict(counts),
            "latest_loop": loop, "latest_s3": context.get("latest_s3", {}),
            "zero_candidate_seconds": zero_signal_seconds, "alerts": alerts,
        }
        with metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(metric, sort_keys=True) + "\n")
        state["last_log_size"] = log_path.stat().st_size if log_path.exists() else 0
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        sys.stdout.write(json.dumps(metric, sort_keys=True) + "\n")
        sys.stdout.flush()
        time.sleep(min(args.interval_seconds, max(0.1, deadline - time.time())))

    elapsed = max(0.0, time.time() - started.timestamp())
    report = {
        "status": "COMPLETED" if elapsed >= args.hours * 3600 and not stop else "STOPPED_PENDING",
        "started_at": started.isoformat(), "ended_at": now_iso(),
        "elapsed_seconds": elapsed, "required_seconds": args.hours * 3600,
        "pids_at_finish": bot_pids(), "active_vetoes": active_vetoes(Path(args.config)),
        "counts": dict(counts), "strategy_reasons": dict(strategy_reasons),
        "vetoes": dict(vetoes), "news_sources": dict(news_sources),
    }
    summary_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
