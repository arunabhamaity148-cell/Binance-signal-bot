"""Staged application boot and signal-only runtime entry point."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import signal as os_signal
from dataclasses import dataclass
from typing import Callable, Mapping

from app.config import AppConfig, get_assumed_account_equity_usd, load_and_validate_all
from app.core.errors import MissingAssumedEquityError
from app.core.logging import get_logger
from app.data.binance.models import RawExchangeInfoSymbol
from app.bot import SignalBot, SnapshotReadinessError

logger=get_logger(__name__)
BOOT_SNAPSHOT_TIMEOUT_S=90.0  # class E: bounded first-snapshot wait


class BootStageError(RuntimeError):
    def __init__(self,stage:str,message:str):
        self.stage=stage; super().__init__(message)


class UniverseValidationError(BootStageError):
    def __init__(self,accepted:list[str],rejected:list[str],minimum:int):
        self.accepted_symbols=list(accepted); self.rejected_symbols=list(rejected)
        detail="\n".join(f"  - {item}" for item in rejected) or "  - no symbols accepted"
        super().__init__("exchange_info_validation",f"only {len(accepted)} of {len(accepted)+len(rejected)} configured symbols validated; need at least {minimum}. Rejected symbols:\n{detail}")


@dataclass
class BootDependencies:
    config_loader:Callable=load_and_validate_all
    equity_loader:Callable=get_assumed_account_equity_usd
    bot_factory:Callable=SignalBot
    environ:Mapping[str,str]|None=None

    def environment(self): return os.environ if self.environ is None else self.environ


def validate_exchange_universe(cfg:AppConfig,entries:list[RawExchangeInfoSymbol|dict])->tuple[list[str],list[str]]:
    """Validate every enabled configured pair against public exchangeInfo; never substitute symbols."""
    configured=cfg.enabled_symbols(); grouped={}
    for entry in entries:
        symbol=entry.get("symbol", "<unknown>") if isinstance(entry,dict) else entry.symbol
        grouped.setdefault(symbol,[]).append(entry)
    accepted=[]; rejected=[]
    for symbol in configured:
        matches=grouped.get(symbol,[])
        reason=None
        if not matches: reason="not present in exchangeInfo"
        elif len(matches)!=1: reason="duplicate exchangeInfo entries"
        elif isinstance(matches[0],dict): reason=matches[0].get("invalid_reason","malformed exchangeInfo row")
        else:
            item=matches[0]
            if item.status!="TRADING": reason=f"status is {item.status!r}, not 'TRADING'"
            elif item.price_tick<=0: reason=f"invalid PRICE_FILTER tickSize={item.price_tick}"
            elif item.qty_step<=0: reason=f"invalid LOT_SIZE stepSize={item.qty_step}"
            elif item.min_qty<=0: reason=f"invalid LOT_SIZE minQty={item.min_qty}"
        if reason: rejected.append(f"{symbol}: {reason}"); continue
        accepted.append(symbol)
        configured_pair=cfg.pair_config(symbol)
        if (item.price_tick!=configured_pair["price_tick"] or item.qty_step!=configured_pair["qty_step"] or item.min_qty!=configured_pair["min_qty"]):
            logger.warning("exchange filters differ from local advisory config",extra={"context":{"symbol":symbol,"exchange_tick":item.price_tick,"configured_tick":configured_pair["price_tick"],"exchange_step":item.qty_step,"configured_step":configured_pair["qty_step"],"exchange_min_qty":item.min_qty,"configured_min_qty":configured_pair["min_qty"]}})
    minimum=int(cfg.system["boot_checks"]["min_symbols_validated"])
    if rejected:
        logger.warning("exchangeInfo rejected configured symbols",extra={"context":{"rejected":rejected,"accepted":len(accepted)}})
    if len(accepted)<minimum: raise UniverseValidationError(accepted,rejected,minimum)
    return accepted,rejected


async def run_application(*,config_dir=None,dependencies:BootDependencies|None=None,stage_callback=None,stop_event=None):
    deps=dependencies or BootDependencies(); bot=None; stage="configuration_validation"
    async def announce(name):
        nonlocal stage
        stage=name; logger.info("boot stage",extra={"context":{"stage":name}})
        if stage_callback: stage_callback(name)
    try:
        await announce("configuration_validation")
        cfg=deps.config_loader(config_dir)
        await announce("assumed_equity_validation")
        try: equity=deps.equity_loader()
        except MissingAssumedEquityError as exc: exc.boot_stage=stage; raise
        if not math.isfinite(equity) or equity<=0: raise MissingAssumedEquityError("assumed equity must be finite and > 0")
        await announce("trading_credential_guard")
        env=deps.environment(); forbidden=cfg.system["forbidden_credentials"]
        present=[name for name in forbidden if name in env]
        if present: raise BootStageError(stage,f"trading credentials are present in environment: {present}; refusing signal-bot startup")
        bot=deps.bot_factory(cfg,equity)
        await announce("exchange_info_validation")
        accepted,_rejected=validate_exchange_universe(cfg,await bot.exchange_info())
        await announce("websocket_first_snapshot")
        await bot.start_market_data(accepted)
        try: await bot.wait_for_snapshot_readiness(accepted,timeout_s=BOOT_SNAPSHOT_TIMEOUT_S,
            min_ready_count=min(len(accepted),int(cfg.system["boot_checks"]["min_symbols_validated"])))
        except SnapshotReadinessError as exc:
            raise BootStageError(stage,f"first full snapshots not ready before {BOOT_SNAPSHOT_TIMEOUT_S:.0f}s timeout; symbols still not ready: {exc.symbols}") from exc
        await announce("news_and_main_evaluation")
        await bot.start_news_collectors()
        await bot.run(stop_event)
    except BaseException as exc:
        if not hasattr(exc,"boot_stage"):
            try: exc.boot_stage=stage
            except Exception: pass
        logger.exception("application stopped",extra={"context":{"stage":stage,"error_type":type(exc).__name__}})
        raise
    finally:
        if bot is not None:
            await bot.shutdown()


async def _run_with_signals(dependencies=None)->None:
    stop_event=asyncio.Event(); loop=asyncio.get_running_loop()
    installed=[]
    for sig in (os_signal.SIGINT,os_signal.SIGTERM):
        try: loop.add_signal_handler(sig,stop_event.set); installed.append(sig)
        except RuntimeError as exc:
            logger.warning("OS signal handler unavailable",extra={"context":{"signal":sig.name,"error":str(exc)}})
    try: await run_application(dependencies=dependencies,stop_event=stop_event)
    finally:
        for sig in installed:
            try: loop.remove_signal_handler(sig)
            except Exception: pass


def main(*,dependencies:BootDependencies|None=None)->int:
    try:
        asyncio.run(_run_with_signals(dependencies))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error("startup/runtime failed",extra={"context":{"stage":getattr(exc,"boot_stage","unknown"),"error":str(exc)}})
        return 1


if __name__=="__main__": raise SystemExit(main())
