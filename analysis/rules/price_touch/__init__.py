"""price_touch — 当天触线候选，证据来自 result + line_events。"""
from .config import PriceTouchConfig
from .detect import run_detection

RULE_META = {
    "name": "price_touch",
    "upstream_algo": "fib_retracement",
    "config_class": PriceTouchConfig,
    "detect_fn": run_detection,
}
