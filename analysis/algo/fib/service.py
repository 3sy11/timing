"""FibService：管理 fib levels 状态，对外暴露 fit / get_levels / set_state。"""
from typing import Dict, List, Optional, Tuple
from bollydog.models.service import AppService
from timing.analysis.config import FibConfig
from timing.analysis.types import FibResult, PriceCluster
from .fitting import fit_fib


class FibService(AppService):
    domain = "timing"
    alias = "FibService"
    commands = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state: Dict[Tuple[str, str], FibResult] = {}

    def compute_and_store(self, symbol: str, interval: str,
                          clusters_high: List[PriceCluster], clusters_low: List[PriceCluster],
                          config: FibConfig) -> Optional[FibResult]:
        result = fit_fib(clusters_high, clusters_low, config)
        if result:
            self._state[(symbol, interval)] = result
        return result

    def set_state(self, symbol: str, interval: str, result: FibResult):
        self._state[(symbol, interval)] = result

    def get_result(self, symbol: str, interval: str) -> Optional[FibResult]:
        return self._state.get((symbol, interval))

    def get_levels(self, symbol: str, interval: str) -> Optional[List[Tuple[float, float]]]:
        r = self._state.get((symbol, interval))
        return r.levels if r else None

    async def on_reset(self) -> None:
        self._state.clear()
