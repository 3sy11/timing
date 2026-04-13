"""分析引擎全局参数配置。回测时替换 FibConfig 实例即可切换参数。"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


@dataclass
class FibConfig:
    pivot_windows: List[Tuple[int, int]] = field(default_factory=lambda: [(5, 5), (8, 8)])
    zigzag_thresholds: List[float] = field(default_factory=lambda: [0.05, 0.10])
    regression_windows: List[int] = field(default_factory=lambda: [50, 100])
    weights: Dict[str, float] = field(default_factory=lambda: {
        'pivot_5': 0.5, 'pivot_8': 1.0,
        'zigzag_5': 0.5, 'zigzag_10': 1.0,
        'reg_50': 0.5, 'reg_100': 1.0,
    })
    cluster_tolerance_pct: float = 0.005
    min_cluster_conf: float = 0.3
    min_span_pct: float = 0.10
    max_ratio_error: float = 0.05
    std_ratios: Tuple[float, ...] = DEFAULT_RATIOS
    touch_tolerance: float = 0.5
    touch_cooldown_sec: float = 60.0
