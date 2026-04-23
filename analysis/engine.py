"""AnalysisEngine — 管理算法子服务容器，不参与 subscriber/缓存。"""
from timing.analysis.algo.retracement.config import RetracementConfig
from timing.analysis.algo.retracement.service import RetracementService
from bollydog.models.service import AppService


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"

    def __init__(self, config: RetracementConfig = None, data_path: str = "", **kwargs):
        super().__init__(**kwargs)
        self.retracement = RetracementService(config=config or RetracementConfig(), data_path=data_path)
        self.add_dependency(self.retracement)

    @property
    def config(self): return self.retracement.config
