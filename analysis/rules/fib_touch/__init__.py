"""fib_touch Rule — Fibonacci 触线信号检测。

管理单元：绑定上游 algo、配置 schema、检测入口、profiles 目录。
"""
from .config import FibTouchConfig
from .detect import run_detection

RULE_META = {
    "name": "fib_touch",
    "upstream_algo": "fib_retracement",
    "config_class": FibTouchConfig,
    "detect_fn": run_detection,
}
