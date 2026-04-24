"""TimingApp / BacktestApp — 同级应用，各自持有独立服务树。"""
import os, logging
from typing import Dict
from bollydog.models.service import AppService
from timing.common.clock import LiveClock, SimulatedClock
from timing.data.engine import DataEngine
from timing.data.config import DataConfig
from timing.analysis.engine import AnalysisEngine
from timing.analysis.algo.retracement.config import RetracementConfig

log = logging.getLogger(__name__)


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []

    def __init__(self, clock=None, data_dir="cache/", **kwargs):
        super().__init__(**kwargs)
        if clock is None: self.clock = LiveClock()
        elif isinstance(clock, type): self.clock = clock()
        else: self.clock = clock
        os.makedirs(data_dir, exist_ok=True)
        self.data = DataEngine()
        self.analysis = AnalysisEngine(clock=self.clock, data_engine=self.data)
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)


class BacktestApp(AppService):
    """与 TimingApp 同级的回测应用，SimulatedClock + 隔离 db_path。"""
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, clock=None, data_dir="cache/", analysis_dir="cache/backtest/", **kwargs):
        super().__init__(**kwargs)
        self.clock = clock or SimulatedClock()
        self.analysis_dir = analysis_dir
        os.makedirs(analysis_dir, exist_ok=True)
        self.data = DataEngine(config=DataConfig(db_path=os.path.join(data_dir, "data.duckdb")))
        self.analysis = AnalysisEngine(
            config=RetracementConfig(db_path=os.path.join(analysis_dir, "retracement.sqlite")),
            clock=self.clock, data_engine=self.data)
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)
        self._results: Dict[str, dict] = {}
        log.info(f'[BacktestApp] init data_dir={data_dir} analysis_dir={analysis_dir}')

    def get_result(self, symbol: str, interval: str) -> dict:
        return self._results.get(f"{symbol}:{interval}", {})

    def set_result(self, symbol: str, interval: str, report: dict):
        self._results[f"{symbol}:{interval}"] = report
