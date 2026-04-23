"""Retracement command：OnBarReceived 订阅 PushBars，编排纯函数 + 读写缓存 + 发布事件。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from .models import FibLevelTouched, FibInvalidated

log = logging.getLogger(__name__)


class OnBarReceived(BaseCommand):
    """订阅 timing.DataEngine.PushBars → 碰撞/突破检测 + 按需重算。

    destination 属于 RetracementService，所以 app 就是 RetracementService 实例。
    """
    destination: ClassVar[str] = "timing.RetracementService.OnBarReceived"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        symbol, interval = info.get("symbol", ""), info.get("interval", "")
        bars = info.get("bars", [])
        if not (symbol and bars): return None
        from .algo import check_touch_with_cooldown, check_breakout, compute_retracement
        svc = app  # app 就是 RetracementService
        cfg = svc.config
        summary = {"touched_total": 0, "broken_total": 0, "recomputed": False}
        for bar in bars:
            close = float(bar.get("close", 0))
            if not close: continue
            all_levels = svc.get_all_levels(symbol, interval)
            touched = check_touch_with_cooldown(close, all_levels, cfg.touch_tolerance, cfg.touch_cooldown_sec, svc.touch_last, (symbol, interval))
            for r, p in touched:
                await hub.emit(FibLevelTouched(symbol=symbol, ratio=r, level_price=p, touch_price=close))
            summary["touched_total"] += len(touched)
            cache = svc.get_cache(symbol, interval)
            groups = cache.get("groups", []) if cache else []
            broken = check_breakout(close, groups, tolerance=cfg.breakout_tolerance)
            summary["broken_total"] += len(broken)
            if broken:
                for idx, direction, side in broken:
                    await hub.emit(FibInvalidated(symbol=symbol, interval=interval, group_idx=idx, direction=direction, break_side=side, close=close))
                if cache:
                    broken_idx = {idx for idx, _, _ in broken}
                    cache["groups"] = [g for i, g in enumerate(groups) if i not in broken_idx]
                data_engine = hub.get_service("timing.DataEngine")
                klines = data_engine.get_klines(symbol, interval) if data_engine else []
                if klines:
                    new_result = compute_retracement(klines, cfg)
                    await svc.set_cache(symbol, interval, new_result)
                    summary["recomputed"] = True
                elif cache:
                    await svc.set_cache(symbol, interval, cache)
        if summary["touched_total"] or summary["broken_total"]:
            log.info(f'[Retracement] OnBarReceived {symbol}/{interval} touched={summary["touched_total"]} broken={summary["broken_total"]} recomputed={summary["recomputed"]}')
        return summary


class ComputeRetracement(BaseCommand):
    """指定 symbol/interval，从 DataEngine 取全量 klines 重算 → 写入 RetracementService 缓存。"""
    destination: ClassVar[str] = "timing.RetracementService.ComputeRetracement"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.symbol and self.interval): return None
        data_engine = hub.get_service("timing.DataEngine")
        if not data_engine:
            log.warning('[Retracement] ComputeRetracement: DataEngine not found')
            return None
        klines = data_engine.get_klines(self.symbol, self.interval)
        if not klines:
            log.warning(f'[Retracement] ComputeRetracement: no klines for {self.symbol}/{self.interval}')
            return None
        from .algo import compute_retracement
        svc = app
        result = compute_retracement(klines, svc.config)
        await svc.set_cache(self.symbol, self.interval, result)
        groups = result.get("groups", [])
        log.info(f'[Retracement] ComputeRetracement {self.symbol}/{self.interval} klines={len(klines)} groups={len(groups)} legs_found={result.get("legs_found",0)} legs_kept={result.get("legs_kept",0)}')
        return {"symbol": self.symbol, "interval": self.interval, "groups": len(groups), "klines": len(klines)}
