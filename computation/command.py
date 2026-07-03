"""Compute command — 统一计算入口。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Compute(BaseCommand):
    """触发指定算法的完整计算管道。"""
    destination: ClassVar[str] = "computation.ComputationService.Compute"
    algo: str = ""
    compute_id: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.algo and self.compute_id and self.symbol and self.interval):
            log.error('[计算] Compute 缺少必要参数: algo, compute_id, symbol, interval')
            return None
        from .algo.registry import ALGO_REGISTRY
        pipeline_fn = ALGO_REGISTRY.get(self.algo)
        if not pipeline_fn:
            log.error(f'[计算] 未知算法: {self.algo}, 可用: {list(ALGO_REGISTRY.keys())}')
            return None
        from timing.integration.app import IntegrationService
        from bollydog.globals import services
        integration = None
        for svc in services.values():
            if isinstance(svc, IntegrationService):
                integration = svc; break
        if not integration:
            log.error('[计算] 未找到 IntegrationService，无法读取 klines')
            return None
        klines = integration.get_klines(self.symbol, self.interval)
        if not klines:
            log.error(f'[计算] 无 klines 数据: {self.symbol}/{self.interval}')
            return None
        from .writer import StepWriter
        writer = StepWriter(warehouse=app.warehouse_path, algo=self.algo,
                           compute_id=self.compute_id, symbol=self.symbol, interval=self.interval)
        cfg_dict = getattr(app, 'config', None) or {}
        from .algo.fib_retracement.config import RetracementConfig
        cfg = RetracementConfig(**(cfg_dict if isinstance(cfg_dict, dict) else {}))
        log.info(f'[计算] 开始 {self.algo}/{self.compute_id} symbol={self.symbol} interval={self.interval} klines={len(klines)}')
        result = pipeline_fn(klines, cfg, writer)
        log.info(f'[计算] 完成 {self.algo}/{self.compute_id} → {result}')
        return result
