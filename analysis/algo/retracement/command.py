"""Retracement command：OnBarReceived / ComputeRetracement 薄壳委托。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from .models import FibLevelTouched, FibInvalidated

log = logging.getLogger(__name__)


class OnBarReceived(BaseCommand):
    """订阅 PushBars → 逐 bar 调用 svc.on_bar → 根据返回值发出事件。"""
    destination: ClassVar[str] = "timing.RetracementService.OnBarReceived"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        symbol, interval = info.get("symbol", ""), info.get("interval", "")
        bars = info.get("bars", [])
        if not (symbol and bars): return None
        svc = app
        summary = {"touched": 0, "broken": 0, "recomputed": False}
        for bar in bars:
            r = await svc.on_bar(symbol, interval, bar)
            for sig in r["signals"]:
                await hub.emit(FibLevelTouched(symbol=symbol, ratio=sig["ratio"], level_price=sig["level_price"], touch_price=sig["touch_price"]))
                summary["touched"] += 1
            for b in r["breakouts"]:
                await hub.emit(FibInvalidated(symbol=symbol, interval=interval, group_idx=b["group_idx"], direction=b["direction"], break_side=b["break_side"], close=float(bar.get("close", 0))))
                summary["broken"] += 1
            if r["recomputed"]: summary["recomputed"] = True
        if summary["touched"] or summary["broken"]:
            log.info(f'[Retracement] {symbol}/{interval} touched={summary["touched"]} broken={summary["broken"]} recomputed={summary["recomputed"]}')
        return summary


class ComputeRetracement(BaseCommand):
    """compute_retracement → set_cache。klines 可外部传入，缺省从 data_engine 取。"""
    destination: ClassVar[str] = "timing.RetracementService.ComputeRetracement"
    symbol: str = ""
    interval: str = ""
    klines: list = None

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.symbol and self.interval): return None
        svc = app
        klines = self.klines
        if not klines:
            if not svc.data_engine:
                log.warning('[Retracement] ComputeRetracement: data_engine not set'); return None
            klines = svc.data_engine.get_klines(self.symbol, self.interval)
        if not klines:
            log.warning(f'[Retracement] ComputeRetracement: no klines for {self.symbol}/{self.interval}'); return None
        from .algo import compute_retracement
        result = compute_retracement(klines)
        await svc.set_cache(self.symbol, self.interval, result)
        groups = result.get("groups", [])
        log.info(f'[Retracement] ComputeRetracement {self.symbol}/{self.interval} klines={len(klines)} groups={len(groups)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": len(klines),
                "groups": len(groups), "legs_found": result.get("legs_found", 0), "legs_kept": result.get("legs_kept", 0)}
