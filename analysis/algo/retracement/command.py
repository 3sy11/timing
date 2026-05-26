"""Retracement commands — ComputeRetracement + GetSignals。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class ComputeRetracement(BaseCommand):
    """compute_retracement → set_cache。klines 可外部传入，缺省从 DataEngine 取。"""
    destination: ClassVar[str] = "analysis.RetracementService.ComputeRetracement"
    symbol: str = ""
    interval: str = ""
    klines: list = None

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.symbol and self.interval): return None
        svc = app
        klines = self.klines
        if not klines:
            from timing.data.models import GetKlines
            get_cmd = GetKlines(symbol=self.symbol, interval=self.interval)
            klines = await hub.execute(get_cmd)
        if not klines:
            log.warning(f'[Retracement] ComputeRetracement: no klines for {self.symbol}/{self.interval}'); return None
        from .algo import compute_retracement
        result = compute_retracement(klines, svc.config)
        await svc.set_cache(self.symbol, self.interval, result)
        groups = result.get("groups", [])
        log.info(f'[Retracement] ComputeRetracement {self.symbol}/{self.interval} klines={len(klines)} groups={len(groups)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": len(klines),
                "groups": len(groups), "legs_found": result.get("legs_found", 0), "legs_kept": result.get("legs_kept", 0)}


class GetSignals(BaseCommand):
    destination: ClassVar[str] = "analysis.RetracementService.GetSignals"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if not app.protocol: return []
        return await app.protocol.get(f"signals:{self.symbol}:{self.interval}") or []
