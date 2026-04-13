"""TimingApp 中枢：统一入口，按依赖顺序启动各引擎。
启动顺序：CacheEngine → DataEngine → AnalysisEngine。
YAML 可传 clock: !module timing.common.clock.SimulatedClock。"""
import os
from timing.common.clock import Clock, LiveClock
from timing.engine.cache import CacheEngine
from timing.data.engine import DataEngine
from timing.analysis.engine import AnalysisEngine
from bollydog.models.service import AppService
from bollydog.adapters.rdb import DuckDBProtocol


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []

    def __init__(self, clock=None, **kwargs):
        super().__init__(**kwargs)
        if clock is None: self.clock = LiveClock()
        elif isinstance(clock, type): self.clock = clock()
        else: self.clock = clock
        os.makedirs('tmp', exist_ok=True)
        data_proto = DuckDBProtocol(url='tmp/timing.duckdb')
        analysis_proto = DuckDBProtocol(url='tmp/timing.duckdb')
        self.cache = CacheEngine()
        self.data = DataEngine(protocol=data_proto)
        self.analysis = AnalysisEngine(clock=self.clock, protocol=analysis_proto)
        self.add_dependency(self.cache)
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)
