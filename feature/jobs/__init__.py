"""Feature job 注册表。"""
from typing import Dict

from .price_touch import JOB_META as price_touch_meta

JOB_REGISTRY: Dict[str, dict] = {
    "price_touch": price_touch_meta,
}


def discover_jobs() -> Dict[str, dict]:
    return dict(JOB_REGISTRY)
