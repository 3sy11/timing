"""SwingService：管理缓存，计算委托给 command.py 纯函数。"""
from typing import Dict, List, Tuple
from bollydog.models.service import AppService
from timing.analysis.config import FibConfig
from timing.analysis.types import PriceCluster
from .command import compute_swing_features


class SwingService(AppService):
    domain = "timing"
    alias = "SwingService"
    commands = ["timing.analysis.algo.swing.command"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cache: Dict[Tuple[str, str], Tuple[List[PriceCluster], List[PriceCluster]]] = {}

    def compute_features(self, klines: List[dict], config: FibConfig) -> Tuple[List[PriceCluster], List[PriceCluster]]:
        """编排用：调用纯函数并还原为 PriceCluster 对象。"""
        from dataclasses import fields as dc_fields
        result = compute_swing_features(klines, {f.name: getattr(config, f.name) for f in dc_fields(config)})
        ch = [PriceCluster(**d) for d in result["clusters_high"]]
        cl = [PriceCluster(**d) for d in result["clusters_low"]]
        return ch, cl

    def set_cache(self, symbol: str, interval: str, clusters_high: List[PriceCluster], clusters_low: List[PriceCluster]):
        self._cache[(symbol, interval)] = (clusters_high, clusters_low)

    def get_cache(self, symbol: str, interval: str):
        return self._cache.get((symbol, interval))

    async def on_reset(self) -> None:
        self._cache.clear()
