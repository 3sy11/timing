"""fib_signal — 对 candidates 做硬过滤和打分，写出信号。不算入场/止损。"""
from .config import FibSignalConfig
from .detect import run_detection

RULE_META = {
    "name": "fib_signal",
    "upstream_algo": "fib_retracement",
    "config_class": FibSignalConfig,
    "detect_fn": run_detection,
}
