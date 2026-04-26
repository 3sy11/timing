"""AnalysisEngine — TOML 配置驱动的算法子服务容器。
使用 TOMLFileProtocol 持久化分析配置，启动时读取并覆盖子服务模块常量。
对外暴露 apply_config / warmup 通用接口，bar 处理由 subscriber 链驱动。
"""
import os, logging
from typing import List, Optional
from bollydog.adapters.file import TOMLFileProtocol
from bollydog.models.service import AppService
from timing.analysis.algo.retracement.service import RetracementService

log = logging.getLogger(__name__)

# 分析引擎缓存根目录，子服务的 sqlite / 持久化文件均在此目录下
CACHE_PATH: str = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"

    def __init__(self, clock=None, data_engine=None, cache_path: str = None, **kwargs):
        global CACHE_PATH
        if cache_path: CACHE_PATH = cache_path
        os.makedirs(CACHE_PATH, exist_ok=True)
        proto = TOMLFileProtocol(path=os.path.join(CACHE_PATH, "config.toml"))
        super().__init__(protocol=proto, **kwargs)
        self._sub_configs = {}
        self._sub_services = {}
        self.retracement = RetracementService(clock=clock, data_engine=data_engine, cache_path=CACHE_PATH)
        self.add_dependency(self.retracement)

    def add_dependency(self, dep):
        super().add_dependency(dep)
        alias = getattr(dep, 'alias', None)
        if not alias: return
        cfg_mod = getattr(dep, 'config', None)
        if cfg_mod and hasattr(cfg_mod, 'apply_overrides'):
            self._sub_configs[alias] = cfg_mod
        self._sub_services[alias] = dep

    async def on_start(self) -> None:
        if self.protocol: await self.protocol.maybe_start()
        data = await self.protocol.read()
        for key, cfg in data.items():
            if not isinstance(cfg, dict) or key not in self._sub_configs: continue
            applied = self._sub_configs[key].apply_overrides(cfg)
            if applied: log.info(f'[AnalysisEngine] {key} config overrides: {applied}')
        await super(AppService, self).on_start()
        self._load_commands(self.commands)

    # ═══════ 运行时配置合并 ═══════

    def apply_config(self, overrides: dict):
        """运行时配置覆盖，overrides 按子服务 alias 分区。"""
        for alias, cfg in overrides.items():
            if not isinstance(cfg, dict) or alias not in self._sub_configs: continue
            applied = self._sub_configs[alias].apply_overrides(cfg)
            if applied: log.info(f'[AnalysisEngine] apply_config {alias}: {applied}')

    # ═══════ 通用分析接口（回测 / 外部统一调用）═══════

    def _iter_services(self, services: Optional[List[str]] = None):
        for alias, svc in self._sub_services.items():
            if services and alias not in services: continue
            yield alias, svc

    async def warmup(self, symbol: str, interval: str, klines: list, services: Optional[List[str]] = None):
        """顺序执行子服务 warmup，klines 由调用方提前切好。"""
        for alias, svc in self._iter_services(services):
            if not hasattr(svc, 'warmup'): continue
            await svc.warmup(symbol, interval, klines)
            log.info(f'[AnalysisEngine] warmup {alias} {symbol}/{interval} bars={len(klines)}')

    # ═══════ 配置持久化读写 ═══════

    async def get_symbol_overrides(self, symbol: str, interval: str) -> dict:
        return await self.protocol.get(f"symbols.{symbol}:{interval}", {})

    async def set_symbol_overrides(self, symbol: str, interval: str, overrides: dict):
        await self.protocol.set(f"symbols.{symbol}:{interval}", overrides)
        log.info(f'[AnalysisEngine] set_symbol_overrides {symbol}/{interval} keys={list(overrides.keys())}')
