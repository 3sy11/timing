"""price_touch — 当天触线宽表，证据来自 result + line_events。"""
from .config import PriceTouchConfig
from .detect import run_detection

JOB_META = {
    "name": "price_touch",
    "upstream_algo": "fib_retracement",
    "config_class": PriceTouchConfig,
    "build_fn": run_detection,
}
