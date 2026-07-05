"""Rule 注册表 — 所有分析规则在此注册。"""
from typing import Dict

from .fib_touch import RULE_META as fib_touch_meta

RULE_REGISTRY: Dict[str, dict] = {
    "fib_touch": fib_touch_meta,
}


def discover_rules() -> Dict[str, dict]:
    """返回所有已注册的 Rule 元信息。"""
    return dict(RULE_REGISTRY)
