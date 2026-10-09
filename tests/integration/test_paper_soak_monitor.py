from __future__ import annotations

import json

import pytest

from scripts.paper_soak_monitor import main


def test_paper_soak_monitor_records_live_log_events_and_pending_summary(tmp_path, capsys):
    log = tmp_path / "bot.log"
    log.write_text(
        "\n".join([
            "[INFO] app.bot: loop_health | {'cycles': 2, 'candidates_last_5min': 1, 'signals_last_5min': 1, 'ws_connected': True, 'snapshots_ready': 20, 'snapshot_total': 20}",
            '[INFO] app.monitoring.diagnostics: diag_strategy | {"strategy": "S1", "reason": "sweep_setup_valid", "decision": "candidate"}',
            "[INFO] app.strategies.s3_funding_crowding: s3_data_check | {'symbol': 'BTCUSDT', 'reason': 'ls_ratio_stale', 'ls_ratio_age_ms': 900000}",
            "[WARNING] app.news.engine: news_source_failed | {'source': 'gdelt', 'error': 'timed out'}",
            "[INFO] app.monitoring.diagnostics: diag_veto | {\"guard\": \"G2\", \"decision\": \"PASS\", \"reason\": null}",
        ]) + "\n"
    )
    output = tmp_path / "soak"
    rc = main([
        "--log", str(log),
        "--output-dir", str(output),
        "--config", "config/veto.yaml",
        "--hours", "0.0000001",
        "--interval-seconds", "0.001",
    ])
    assert rc == 0
    assert "PAPER SOAK MONITOR STARTED" in capsys.readouterr().out
    metrics = [json.loads(line) for line in (output / "metrics.jsonl").read_text().splitlines()]
    assert metrics
    cumulative = metrics[0]["cumulative"]
    assert cumulative["loop_health"] == 1
    assert cumulative["diag_strategy"] == 1
    assert cumulative["s3_ls_ratio_stale"] == 1
    assert cumulative["news_timeout"] == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "COMPLETED"
    assert summary["counts"]["diag_veto"] == 1


def test_paper_soak_monitor_rejects_non_positive_duration(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--log", str(tmp_path / "missing.log"), "--hours", "0"])
    assert exc.value.code == 2
