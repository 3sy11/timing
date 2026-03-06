"""触线检测：价格与回撤线距离 <= 容差视为触碰；可选去抖。"""
from typing import List, Tuple
import time


def check_touch(price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    """levels 为 [(ratio, price), ...]，返回触中的 [(ratio, level_price), ...]。"""
    return [(r, p) for r, p in levels if abs(price - p) <= tolerance]


class TouchDetector:
    """带去抖：同一 (ratio, level) 在冷却时间内不重复视为触线。"""

    def __init__(self, cooldown_sec: float = 60.0):
        self.cooldown = cooldown_sec
        self._last: dict = {}  # (ratio, level) -> last_touch_ts

    def check(self, price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
        now = time.time()
        touched = check_touch(price, levels, tolerance)
        out = []
        for r, p in touched:
            key = (r, p)
            if now - self._last.get(key, 0) >= self.cooldown:
                out.append((r, p))
                self._last[key] = now
        return out
