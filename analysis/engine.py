"""分析引擎 AppService。编排层：对外暴露 recompute_fib / check_touch，
内部子服务（SwingService / FibService / DetectorService）对 Command 不可见。"""
import json, logging, time
from typing import List, Optional, Tuple
from timing.analysis.config import FibConfig
from timing.analysis.models import OnBarForAnalysis, OnCacheIngested
from timing.analysis.algo.swing.service import SwingService
from timing.analysis.algo.fib.service import FibService
from timing.analysis.algo.detector.service import DetectorService
from timing.analysis.types import FibResult
from timing.common.clock import Clock, LiveClock
from bollydog.models.service import AppService

log = logging.getLogger(__name__)

_ANALYSIS_TABLES = """
CREATE TABLE IF NOT EXISTS swing_features (
    symbol VARCHAR NOT NULL, interval VARCHAR NOT NULL DEFAULT '',
    computed_at BIGINT NOT NULL, result_json JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS fib_results (
    symbol VARCHAR NOT NULL, interval VARCHAR NOT NULL DEFAULT '',
    computed_at BIGINT NOT NULL, best_h DOUBLE, best_l DOUBLE, score DOUBLE,
    levels_json JSON NOT NULL
);
"""


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"
    commands = ["models", "timing.analysis.algo.swing.command", "timing.analysis.algo.fib.models", "timing.analysis.algo.detector.models"]
    subscriber = {
        "timing.DataEngine.PushBars": OnBarForAnalysis,
        "timing.CacheEngine.OnDataIngested": OnCacheIngested,
    }

    def __init__(self, clock: Clock = None, config: FibConfig = None,
                 protocol=None, router_mapping=None, subscriber=None, **kwargs):
        super().__init__(protocol=protocol, router_mapping=router_mapping, subscribe=subscriber, **kwargs)
        self._load_commands(self.commands)
        self.clock = clock or LiveClock()
        self.config = config or FibConfig()
        self.swing = SwingService()
        self.fib = FibService()
        self.detector = DetectorService(clock=self.clock)
        self.add_dependency(self.swing)
        self.add_dependency(self.fib)
        self.add_dependency(self.detector)

    async def on_started(self) -> None:
        await super().on_started()
        if self.protocol:
            try:
                async with self.protocol.connect() as conn:
                    for stmt in _ANALYSIS_TABLES.strip().split(';'):
                        stmt = stmt.strip()
                        if stmt: conn.execute(stmt)
                log.info('[AnalysisEngine] duckdb tables ready')
            except Exception as e:
                log.warning(f'[AnalysisEngine] duckdb init failed: {e}')

    # ── business methods (Command 只调这些，不碰子服务) ──

    def recompute_fib(self, symbol: str, interval: str, klines: List[dict]) -> Optional[FibResult]:
        ch, cl = self.swing.compute_features(klines, self.config)
        self.swing.set_cache(symbol, interval, ch, cl)
        result = self.fib.compute_and_store(symbol, interval, ch, cl, self.config)
        if result:
            self.detector.reset(symbol, interval)
        return result

    def check_touch(self, symbol: str, interval: str, bars: List[dict]) -> List[Tuple[float, float, float]]:
        levels = self.fib.get_levels(symbol, interval)
        if not levels: return []
        return self.detector.check_bars(symbol, interval, bars, levels, self.config)

    def get_fib_levels(self, symbol: str, interval: str):
        return self.fib.get_levels(symbol, interval)

    async def on_reset(self) -> None:
        pass
