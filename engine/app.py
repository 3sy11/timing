"""TimingApp 中枢：统一入口，按依赖顺序启动各引擎。
启动顺序：DataEngine → AnalysisEngine。
各引擎内部自建 protocol，无需外部传递。"""
import os
from timing.common.clock import Clock, LiveClock
from timing.data.engine import DataEngine
from timing.analysis.engine import AnalysisEngine
from bollydog.models.service import AppService


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []

    def __init__(self, clock=None, **kwargs):
        super().__init__(**kwargs)
        if clock is None: self.clock = LiveClock()
        elif isinstance(clock, type): self.clock = clock()
        else: self.clock = clock
        os.makedirs('cache', exist_ok=True)
        self.data = DataEngine()
        self.analysis = AnalysisEngine()
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)
