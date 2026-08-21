"""price_touch Rule — v3 价格线触碰信号检测。"""
from .config import PriceTouchConfig
from .detect import run_detection

RULE_META = {
    "name": "price_touch",
    "upstream_algo": "fib_retracement",
    "config_class": PriceTouchConfig,
    "detect_fn": run_detection,
}
