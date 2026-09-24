"""Rule 注册表 — 对特征宽表做规则，产出 signals。"""
from typing import Dict

from .fib_signal import RULE_META as fib_signal_meta

RULE_REGISTRY: Dict[str, dict] = {
    "fib_signal": fib_signal_meta,
}


def discover_rules() -> Dict[str, dict]:
    return dict(RULE_REGISTRY)
