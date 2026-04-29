"""AnalysisEngine — TOML 配置驱动的算法子服务容器。

协议链来源（二选一，互斥）：
  1. TOML create_from → _build_protocol → add_dependency 注入
  2. on_init_dependencies 创建默认 TOMLFileProtocol

生命周期：
  on_init_dependencies: 按需创建默认 TOML 协议
  on_start:   _load_commands
  on_started: 从 TOML 读取配置覆盖子服务 Config 实例（children 已就绪）
"""
import os, logging
from bollydog.models.service import AppService
from timing.analysis.algo.retracement.config import RetracementConfig
from timing.analysis.algo.retracement.service import RetracementService

log = logging.getLogger(__name__)

CACHE_PATH: str = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"

    def __init__(self, config: RetracementConfig = None, clock=None, data_engine=None, cache_path: str = None, **kwargs):
        global CACHE_PATH
        if cache_path: CACHE_PATH = cache_path
        self._cache_path = CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        self._sub_configs = {}
        self._sub_services = {}
        super().__init__(**kwargs)
        self.retracement = RetracementService(
            config=config or RetracementConfig(), clock=clock,
            data_engine=data_engine, cache_path=self._cache_path)
        self.add_dependency(self.retracement)

    def on_init_dependencies(self):
        if self.protocol: return []
        from bollydog.adapters.file import TOMLFileProtocol
        proto = TOMLFileProtocol(path=os.path.join(self._cache_path, "config.toml"))
        log.info(f'[AnalysisEngine] default protocol: TOMLFile({self._cache_path}/config.toml)')
        return [proto]

    def add_dependency(self, dep):
        super().add_dependency(dep)
        alias = getattr(dep, 'alias', None)
        if not alias: return dep
        cfg_inst = getattr(dep, 'config', None)
        if cfg_inst and hasattr(cfg_inst, 'apply_overrides'):
            self._sub_configs[alias] = cfg_inst
        self._sub_services[alias] = dep
        return dep

    async def on_start(self) -> None:
        self._load_commands(self.commands)
        await super().on_start()

    async def on_started(self) -> None:
        await super().on_started()
        if not self.protocol: return
        data = await self.protocol.read()
        for key, cfg_overrides in data.items():
            if not isinstance(cfg_overrides, dict) or key not in self._sub_configs: continue
            applied = self._sub_configs[key].apply_overrides(cfg_overrides)
            if applied: log.info(f'[AnalysisEngine] on_started {key} config overrides: {applied}')

    # ═══════ 运行时配置合并 ═══════

    def apply_config(self, overrides: dict):
        for alias, cfg_overrides in overrides.items():
            if not isinstance(cfg_overrides, dict) or alias not in self._sub_configs: continue
            applied = self._sub_configs[alias].apply_overrides(cfg_overrides)
            if applied: log.info(f'[AnalysisEngine] apply_config {alias}: {applied}')

    # ═══════ 配置持久化读写 ═══════

    async def get_symbol_overrides(self, symbol: str, interval: str) -> dict:
        if not self.protocol: return {}
        return await self.protocol.get(f"symbols.{symbol}:{interval}", {})

    async def set_symbol_overrides(self, symbol: str, interval: str, overrides: dict):
        if not self.protocol: return
        await self.protocol.set(f"symbols.{symbol}:{interval}", overrides)
        log.info(f'[AnalysisEngine] set_symbol_overrides {symbol}/{interval} keys={list(overrides.keys())}')
