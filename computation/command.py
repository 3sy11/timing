"""Compute command — 统一计算入口。

流程：load_profile → merge_config → read_klines → pipeline → write_manifest
"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Compute(BaseCommand):
    """触发指定算法的完整计算管道。

    参数优先级：DEFAULTS < profiles/{compute_id}.toml < --override
    """
    destination: ClassVar[str] = "computation.ComputationService.Compute"
    algo: str = ""
    compute_id: str = ""
    symbol: str = ""
    interval: str = ""
    override: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.algo and self.compute_id and self.symbol and self.interval):
            log.error('[计算] Compute 缺少必要参数: algo, compute_id, symbol, interval')
            return None

        from .algo.registry import ALGO_REGISTRY
        pipeline_fn = ALGO_REGISTRY.get(self.algo)
        if not pipeline_fn:
            log.error(f'[计算] 未知算法: {self.algo}, 可用: {list(ALGO_REGISTRY.keys())}')
            return None

        # 1. 加载配置：DEFAULTS < profile < override
        from .algo.fib_retracement.config import RetracementConfig
        override_list = [s.strip() for s in self.override.split(",") if s.strip()] if self.override else []
        cfg = RetracementConfig.from_profile(self.compute_id, override_list)
        log.info(f'[计算] 配置已加载: profile={self.compute_id}, overrides={override_list}')

        # 2. 直接从 Parquet 读取 klines（不依赖 IntegrationService）
        from .reader import read_klines
        klines = read_klines(app.warehouse_path, self.symbol, self.interval)
        if not klines:
            log.error(f'[计算] 无 klines 数据: {self.symbol}/{self.interval}')
            return None

        # 3. 执行管道
        from .writer import StepWriter
        writer = StepWriter(warehouse=app.warehouse_path, algo=self.algo,
                           compute_id=self.compute_id, symbol=self.symbol, interval=self.interval)
        log.info(f'[计算] 开始 {self.algo}/{self.compute_id} symbol={self.symbol} interval={self.interval} klines={len(klines)}')
        result = pipeline_fn(klines, cfg, writer)

        # 4. 写入 manifest.json（实验元数据）
        config_snapshot = {k: v for k, v in cfg.items() if not k.startswith("_")}
        writer.write_manifest(
            config=config_snapshot,
            klines_count=len(klines),
            result_summary=result if isinstance(result, dict) else {},
            config_source=cfg.config_source,
        )

        log.info(f'[计算] 完成 {self.algo}/{self.compute_id} → {result}')
        return result
