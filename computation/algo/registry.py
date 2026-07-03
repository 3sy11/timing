"""算法注册表 — 通过 algo 名路由到对应 pipeline。"""
from .fib_retracement.pipeline import run_pipeline as fib_pipeline

ALGO_REGISTRY = {
    "fib_retracement": fib_pipeline,
}
