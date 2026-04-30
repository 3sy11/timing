"""AnalysisEngine — 具体 Coordinator，持有唯一 subscriber 做 OnBarReceived fan-out。

子服务（RetracementService 等）继承本类，实现 _warmup / on_bar。
生产：PushBars → Exchange → OnBarReceived → fan-out _services
回测：RunBacktest 直接遍历 _services 调 on_bar（保证 bar-by-bar 顺序）。
"""
import os, logging
from typing import ClassVar
from mode.utils.imports import smart_import
from bollydog.models.service import AppService

log = logging.getLogger(__name__)

CLOCK_MODULE = os.environ.get("TIMING_CLOCK", "timing.common.clock.LiveClock")
CACHE_PATH = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService):
    domain = "analysis"
    alias = "AnalysisEngine"
    commands = ["timing.analysis.command"]
    clock = smart_import(CLOCK_MODULE)()
    _services: ClassVar[dict] = {}
    config = None

    def __init_subclass__(cls, abstract=False, **kwargs):
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    async def on_started(self):
        if type(self) is not AnalysisEngine:
            AnalysisEngine._services[self.alias] = self
            log.info(f'[AnalysisEngine] _services registered: {self.alias}')
        await super().on_started()

    async def _warmup(self, symbol, interval, klines):
        raise NotImplementedError

    async def on_bar(self, symbol, interval, bar):
        raise NotImplementedError
