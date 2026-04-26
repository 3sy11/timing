"""TimingApp / BacktestApp — 同级应用，各自持有独立服务树。"""
import os, logging
from bollydog.models.service import AppService
from timing.common.clock import LiveClock, SimulatedClock
from timing.data.engine import DataEngine
from timing.data.config import DataConfig
from timing.analysis.engine import AnalysisEngine

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
    """回测基座：DataEngine(原始数据) + AnalysisEngine + SimulatedClock。
    AnalysisEngine 作为常驻子服务，Hub 启动时 Exchange 自然发现 subscriber。
    每次 RunBacktest 通过 restart() 重置生命周期获得干净状态。
    """
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, clock=None, data_dir="cache/", **kwargs):
        super().__init__(**kwargs)
        self.clock = clock or SimulatedClock()
        self.data = DataEngine(config=DataConfig(db_path=os.path.join(data_dir, "data.duckdb")))
        self.analysis = AnalysisEngine(clock=self.clock, data_engine=self.data,
                                       cache_path=os.path.join(data_dir, "backtest_analysis"))
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)
        log.info(f'[BacktestApp] init data_dir={data_dir}')
