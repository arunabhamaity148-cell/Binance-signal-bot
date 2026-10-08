#!/usr/bin/env python3
"""Generate today's IST paper report from persisted repository data.

Default behavior is a dry run: print the report without contacting Telegram.
Use --send explicitly to deliver it through the configured Telegram sender.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_and_validate_all
from app.database.repository import SignalRepository
from app.telegram.queue import TelegramQueue, build_telegram_limits
from app.telegram.reports import build_daily_report
from app.telegram.sender import SendOutcome, TelegramCredentials, TelegramSender


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print only; never send (default)")
    mode.add_argument("--send", action="store_true", help="send the report to TELEGRAM_CHAT_ID")
    parser.add_argument("--db", help="optional SQLite path override, useful for isolated testing")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    cfg = load_and_validate_all()
    db_path = Path(args.db) if args.db else Path(cfg.config_dir).parent / cfg.system["database_paths"]["sqlite_path"]
    repo = SignalRepository(db_path)
    sender = None
    try:
        await repo.connect()
        report_date, message = await build_daily_report(repo, cfg.risk["paper_trading_assumptions"])
        sys.stdout.write(message + "\n")
        if not args.send:
            sys.stdout.write("\nDRY RUN — Telegram delivery was not attempted.\n")
            return 0

        env_dry = os.getenv("TELEGRAM_DRY_RUN", "true").strip().lower()
        if env_dry not in {"false", "0", "no"}:
            sys.stderr.write("DAILY REPORT: --send requires TELEGRAM_DRY_RUN=false.\n")
            return 2
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            sys.stderr.write("DAILY REPORT: --send requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.\n")
            return 2
        sender = TelegramSender(TelegramCredentials(token, chat, False), TelegramQueue(build_telegram_limits(cfg.system)))
        result = await sender.send_text_message(message)
        if result.outcome != SendOutcome.SENT:
            sys.stderr.write(f"DAILY REPORT: delivery failed for {report_date}: {result.detail}\n")
            return 1
        sys.stdout.write("\nDAILY REPORT: sent.\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"DAILY REPORT: failed: {type(exc).__name__}: {exc}\n")
        return 1
    finally:
        if sender is not None:
            await sender.close()
        await repo.close()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_arguments(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
