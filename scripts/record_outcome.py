#!/usr/bin/env python3
"""Record an operator-entered paper outcome for an already emitted signal.

This tool writes one manual outcome to the local SQLite journal. It does not
observe fills, connect to an exchange, or execute any trade.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_and_validate_all
from app.database.repository import SignalRepository


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-id", required=True, help="existing emitted signal ID")
    parser.add_argument("--realized-r", required=True, type=float,
                        help="operator-calculated outcome in R units; losses are negative")
    parser.add_argument("--note", default="", help="optional operator note")
    parser.add_argument("--db", help="optional SQLite path override, useful for isolated testing")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.realized_r):
        sys.stderr.write("OUTCOME: realized R must be finite.\n")
        return 2
    cfg = load_and_validate_all()
    db_path = Path(args.db) if args.db else Path(cfg.config_dir).parent / cfg.system["database_paths"]["sqlite_path"]
    repository = SignalRepository(db_path)
    try:
        await repository.connect()
        await repository.record_outcome(args.signal_id, args.realized_r, args.note, provenance="manual")
        sys.stdout.write(f"MANUAL OUTCOME RECORDED: {args.signal_id} | {args.realized_r:+.2f}R\n")
        sys.stdout.write("This is operator-entered paper data; no exchange fill was observed.\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"OUTCOME: not recorded: {type(exc).__name__}: {exc}\n")
        return 1
    finally:
        await repository.close()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_arguments(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
