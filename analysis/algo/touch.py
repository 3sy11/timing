"""触线检测与去抖。"""
import time
from typing import List, Tuple


def check_touch(price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    return [(r, p) for r, p in levels if abs(price - p) <= tolerance]


class TouchDetector:
    def __init__(self, cooldown_sec: float = 60.0):
        self.cooldown = cooldown_sec
        self._last: dict = {}

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
