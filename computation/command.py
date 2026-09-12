"""Compute command — 统一计算入口。

流程：load_profile → merge_config → read_klines → pipeline → write_manifest
"""
import logging
import os
from typing import Any, ClassVar
import duckdb
import pandas as pd
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


class RefreshLineFactor(BaseCommand):
    """基于已有 result.parquet 重建 line_factor.parquet，不重新计算 Fib。"""
    destination: ClassVar[str] = "computation.ComputationService.RefreshLineFactor"
    algo: str = "fib_retracement"
    compute_id: str = ""
    symbol: str = ""
    interval: str = ""
    override: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        if self.algo != "fib_retracement" or not (self.compute_id and self.symbol and self.interval):
            log.error('[计算] RefreshLineFactor 需要 fib_retracement、compute_id、symbol、interval')
            return None

        from .algo.fib_retracement.config import RetracementConfig
        from .algo.fib_retracement.line_factors import build_line_factors, to_chinese_line_factor
        from .reader import read_klines
        from .writer import StepWriter

        override_list = [item.strip() for item in self.override.split(",") if item.strip()] if self.override else []
        cfg = RetracementConfig.from_profile(self.compute_id, override_list)
        result_path = os.path.join(
            app.warehouse_path, "computation", self.algo, self.compute_id,
            self.symbol, self.interval, "result.parquet",
        )
        if not os.path.isfile(result_path):
            log.error(f'[计算] result 不存在: {result_path}')
            return None
        with duckdb.connect() as conn:
            result_df = conn.execute("SELECT * FROM read_parquet(?)", [result_path]).df()
        klines = read_klines(app.warehouse_path, self.symbol, self.interval)
        if not klines:
            log.error(f'[计算] 无 klines 数据: {self.symbol}/{self.interval}')
            return None

        factor_df = build_line_factors(
            result_df,
            pd.DataFrame(klines),
            history_bars=cfg.line_factor_history_bars,
            touch_tolerance_pct=cfg.line_factor_touch_tolerance_pct,
            consensus_tolerance_pct=cfg.line_factor_consensus_tolerance_pct,
        )
        writer = StepWriter(app.warehouse_path, self.algo, self.compute_id, self.symbol, self.interval)
        path = writer.write_step("line_factor", to_chinese_line_factor(factor_df))
        log.info(f'[计算] 重建 line_factor：{self.compute_id} → {path} ({len(factor_df)}行)')
        return {"compute_id": self.compute_id, "rows": len(factor_df), "path": path}
