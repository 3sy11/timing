"""FibService：管理 fib levels 状态，计算委托给 command.py 纯函数。"""
from typing import Dict, List, Optional, Tuple
from bollydog.models.service import AppService
from timing.analysis.config import FibConfig
from timing.analysis.types import FibResult, PriceCluster
from .command import fit_fib


class FibService(AppService):
    domain = "timing"
    alias = "FibService"
    commands = ["timing.analysis.algo.fib.command"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state: Dict[Tuple[str, str], FibResult] = {}

    def compute_and_store(self, symbol: str, interval: str,
                          clusters_high: List[PriceCluster], clusters_low: List[PriceCluster],
                          config: FibConfig) -> Optional[FibResult]:
        result = fit_fib(clusters_high, clusters_low, config)
        if result: self._state[(symbol, interval)] = result
        return result

    def get_levels(self, symbol: str, interval: str) -> Optional[List[Tuple[float, float]]]:
        r = self._state.get((symbol, interval))
        return r.levels if r else None

    async def on_reset(self) -> None:
        self._state.clear()
