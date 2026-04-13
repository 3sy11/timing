"""兼容层：从 algo/fib/ 子包重新导出。"""
from timing.analysis.algo.fib.fibonacci import (  # noqa: F401
    DEFAULT_RATIOS, compute_retracement_levels, retracement_from_leg, retracement_from_klines
)
