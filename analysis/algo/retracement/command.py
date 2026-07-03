"""Retracement commands — ComputeRetracement + GetSignals。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class ComputeRetracement(BaseCommand):
    """compute_retracement → 存入 retracements 表。"""
    destination: ClassVar[str] = "analysis.RetracementService.ComputeRetracement"
    symbol: str = ""
    interval: str = ""
    klines: list = None

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.symbol and self.interval): return None
        svc = app
        klines = self.klines
        if not klines:
            from timing.integration.command import GetKlines
            klines = await hub.execute(GetKlines(symbol=self.symbol, interval=self.interval))
        if not klines:
            log.warning(f'[Retracement] ComputeRetracement: no klines for {self.symbol}/{self.interval}'); return None
        from timing.computation.algo.fib_retracement.algo import compute_fib_retracement as compute_retracement
        from .service import _serialize
        result = compute_retracement(klines, svc.cfg)
        await svc.db.put("retracements", {"symbol": self.symbol, "interval": self.interval, "data": _serialize(result)})
        groups = result.get("groups", [])
        log.info(f'[Retracement] ComputeRetracement {self.symbol}/{self.interval} klines={len(klines)} groups={len(groups)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": len(klines),
                "groups": len(groups), "legs_found": result.get("legs_found", 0), "legs_kept": result.get("legs_kept", 0)}


class GetSignals(BaseCommand):
    destination: ClassVar[str] = "analysis.RetracementService.GetSignals"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if not app.db: return []
        return await app.db.get("signals", symbol=self.symbol, interval=self.interval) or []
