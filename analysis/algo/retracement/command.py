"""Retracement command — ComputeRetracement 薄壳委托。

OnBarReceived 已移至 timing/analysis/command.py 做 fan-out。
跨服务数据通过 AppService._apps 查找。
"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService

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
            data_engine = AppService._apps.get('data.DataEngine')
            if not data_engine:
                log.warning('[Retracement] ComputeRetracement: DataEngine not found'); return None
            klines = data_engine.get_klines(self.symbol, self.interval)
        if not klines:
            log.warning(f'[Retracement] ComputeRetracement: no klines for {self.symbol}/{self.interval}'); return None
        from .algo import compute_retracement
        result = compute_retracement(klines, svc.config)
        await svc.set_cache(self.symbol, self.interval, result)
        groups = result.get("groups", [])
        log.info(f'[Retracement] ComputeRetracement {self.symbol}/{self.interval} klines={len(klines)} groups={len(groups)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": len(klines),
                "groups": len(groups), "legs_found": result.get("legs_found", 0), "legs_kept": result.get("legs_kept", 0)}
