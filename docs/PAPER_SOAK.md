# 72-hour paper-soak monitoring

`paper_soak_monitor.py` is a **read-only observer** for the live signal-only bot. It does not call Binance or Telegram, does not place orders, and does not modify the bot's production journal. It tails the existing bot log and records:

- process presence and WebSocket/snapshot health from `loop_health`
- candidate and signal counts
- S1–S5 diagnostic reasons
- active-veto set and veto decisions
- S3 long/short-ratio freshness
- news-source failures and timeouts
- errors, critical events, reconnects, stale/unhealthy events

## Install on the VPS

```bash
cd ~/Binance-signal-bot
sudo install -m 0644 deploy/binance-paper-soak-monitor.service /etc/systemd/system/binance-paper-soak-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now binance-paper-soak-monitor.service
sudo systemctl status binance-paper-soak-monitor.service --no-pager
```

The monitor survives SSH disconnects and restarts after an unexpected exit. It resumes the same run from `runtime/paper_soak/state.json`.

## Start a fresh soak

Do not delete the existing report without saving it. To intentionally start a new 72-hour window:

```bash
sudo systemctl stop binance-paper-soak-monitor.service
cd ~/Binance-signal-bot
rm -rf runtime/paper_soak
sudo systemctl start binance-paper-soak-monitor.service
```

## Observe progress

```bash
sudo journalctl -u binance-paper-soak-monitor.service -f
```

Files written under `runtime/paper_soak/`:

- `state.json` — wall-clock start and log offset
- `metrics.jsonl` — one structured sample per interval
- `summary.json` — final summary after 72 hours or a stopped/pending summary

## Inspect the current summary

```bash
cd ~/Binance-signal-bot
python -m json.tool runtime/paper_soak/summary.json 2>/dev/null || true
tail -n 5 runtime/paper_soak/metrics.jsonl
```

## Completion criteria

The soak is **not** a profitability claim. Mark it operationally complete only when `summary.json` reports `COMPLETED` after at least 72 wall-clock hours and the review confirms:

1. the bot process stayed present or every restart has an explanation;
2. `ws_connected` stayed true and snapshots were ready;
3. no unexplained traceback/critical error occurred;
4. signal and candidate counts are plausible for the market regime;
5. S1–S5 rejection reasons are understood, including intentional S3 stale-data rejects;
6. active vetoes stayed exactly `G1, G2, G4, G5, G6, G8, G9`;
7. no disabled guard unexpectedly evaluated or blocked;
8. news timeouts are understood and do not silently block signals;
9. duplicate-signal and persistence behavior are clean;
10. paper outcomes are recorded separately for later OOS/calibration review.

A zero-signal interval is not automatically a bug. The monitor emits `ZERO_CANDIDATE_WINDOW` after the configured one-hour period since the last candidate; investigate it together with strategy reasons, feed health, S3 freshness, and veto counters.
