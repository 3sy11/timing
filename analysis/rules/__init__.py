"""Rule 注册表 — 所有分析规则在此注册。"""
from typing import Dict

from .price_touch import RULE_META as price_touch_meta

RULE_REGISTRY: Dict[str, dict] = {
    "price_touch": price_touch_meta,
}


def discover_rules() -> Dict[str, dict]:
    return dict(RULE_REGISTRY)
