"""Rule 注册表 — 对特征宽表做规则，产出 signals。当前无已注册规则。"""
from typing import Dict

RULE_REGISTRY: Dict[str, dict] = {}


def discover_rules() -> Dict[str, dict]:
    return dict(RULE_REGISTRY)
