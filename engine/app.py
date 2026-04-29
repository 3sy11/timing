"""TimingApp / BacktestApp — 同级应用，各自持有独立服务树。

TOML 启动：
  bollydog service --config config.toml

每个子服务 (DataEngine, AnalysisEngine, RetracementService) 均可独立作为
TOML 顶层入口配置，包括多层协议链。作为子服务时走 on_init_dependencies 默认链。
"""
import os, logging
from bollydog.models.service import AppService
from timing.common.clock import LiveClock, SimulatedClock
from timing.data.engine import DataEngine
from timing.data.config import DataConfig
from timing.analysis.algo.retracement.config import RetracementConfig
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
        self.data = DataEngine(db_path=os.path.join(data_dir, "data.duckdb"))
        self.analysis = AnalysisEngine(clock=self.clock, data_engine=self.data,
                                       cache_path=os.path.join(data_dir, "analysis"))
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)


class BacktestApp(AppService):
    """回测基座：每次 RunBacktest 通过实例化新子服务实现隔离。"""
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, clock=None, data_dir="cache/backtest/", **kwargs):
        super().__init__(**kwargs)
        self.clock = clock or SimulatedClock()
        os.makedirs(data_dir, exist_ok=True)
        self.data = DataEngine(db_path=os.path.join(data_dir, "data.duckdb"))
        self.analysis = AnalysisEngine(clock=self.clock, data_engine=self.data,
                                       cache_path=os.path.join(data_dir, "analysis"))
        self.add_dependency(self.data)
        self.add_dependency(self.analysis)
        log.info(f'[BacktestApp] init data_dir={data_dir}')
