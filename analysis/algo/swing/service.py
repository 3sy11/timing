"""SwingService：管理缓存，计算委托给 command.py 纯函数。"""
from typing import Dict, List, Tuple
from bollydog.models.service import AppService
from timing.analysis.config import FibConfig
from timing.analysis.types import PriceCluster
from .command import tag_pivots, tag_zigzag, tag_regression, compute_confidence, cluster_prices


class SwingService(AppService):
    domain = "timing"
    alias = "SwingService"
    commands = ["timing.analysis.algo.swing.command"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cache: Dict[Tuple[str, str], Tuple[List[PriceCluster], List[PriceCluster]]] = {}

    def compute_features(self, klines: List[dict], config: FibConfig) -> Tuple[List[PriceCluster], List[PriceCluster]]:
        tags = {}
        tags.update(tag_pivots(klines, config.pivot_windows))
        tags.update(tag_zigzag(klines, config.zigzag_thresholds))
        tags.update(tag_regression(klines, config.regression_windows))
        conf_high, conf_low = compute_confidence(tags, config.weights)
        ch = cluster_prices(klines, conf_high, "high", config.cluster_tolerance_pct, config.min_cluster_conf)
        cl = cluster_prices(klines, conf_low, "low", config.cluster_tolerance_pct, config.min_cluster_conf)
        return ch, cl

    def set_cache(self, symbol: str, interval: str, ch: List[PriceCluster], cl: List[PriceCluster]):
        self._cache[(symbol, interval)] = (ch, cl)

    def get_cache(self, symbol: str, interval: str):
        return self._cache.get((symbol, interval))

    async def on_reset(self) -> None:
        self._cache.clear()
