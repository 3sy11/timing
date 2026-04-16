"""Retracement 算法参数：swing 拐点 + fib 回撤一体化配置。"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


@dataclass
class RetracementConfig:
    # swing 拐点
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
    # fib 回撤
    min_leg_span_pct: float = 0.03
    max_ratio_error: float = 0.05
    std_ratios: Tuple[float, ...] = DEFAULT_RATIOS
    top_n: int = 6
    # 触碰检测：price 在 level ± touch_tolerance 内视为触碰
    touch_tolerance: float = 0.5
    touch_cooldown_sec: float = 60.0
    # 突破判定：close 超出 leg 边界 ± breakout_tolerance 视为突破，触发重算
    breakout_tolerance: float = 1.0
