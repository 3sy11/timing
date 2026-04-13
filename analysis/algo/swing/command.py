"""SwingService 可独立调用的计算命令。
Jupyter 用法：
    from timing.analysis.algo.swing.command import ComputeSwingFeatures
    cmd = ComputeSwingFeatures(klines=klines, config={})
    result = await hub.execute(cmd); data = await cmd.state
    # data["tags"], data["conf_high"], data["clusters_high"] ...
"""
import dataclasses
from typing import Any, ClassVar, Dict, List
from pydantic import Field
from bollydog.models.base import BaseCommand
from timing.analysis.config import FibConfig
from .pivot import tag_pivots
from .zigzag import tag_zigzag
from .regression import tag_regression
from .confidence import compute_confidence
from .cluster import cluster_prices


def compute_swing_features(klines: List[dict], config: dict = None) -> dict:
    """纯函数：step 1-5 全链路，返回每一步中间结果。无 hub 依赖，可直接在 Jupyter 调用。"""
    cfg = FibConfig(**(config or {}))
    tags: Dict[str, List[float]] = {}
    tags.update(tag_pivots(klines, cfg.pivot_windows))
    tags.update(tag_zigzag(klines, cfg.zigzag_thresholds))
    tags.update(tag_regression(klines, cfg.regression_windows))
    conf_high, conf_low = compute_confidence(tags, cfg.weights)
    clusters_high = cluster_prices(klines, conf_high, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    clusters_low = cluster_prices(klines, conf_low, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    return {
        "tags": tags,
        "conf_high": conf_high, "conf_low": conf_low,
        "clusters_high": [dataclasses.asdict(c) for c in clusters_high],
        "clusters_low": [dataclasses.asdict(c) for c in clusters_low],
    }


class ComputeSwingFeatures(BaseCommand):
    """计算拐点特征 step 1-5。入参基础类型，可从 Jupyter 直接 dispatch。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeSwingFeatures"
    klines: List[dict] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        return compute_swing_features(self.klines, self.config)
