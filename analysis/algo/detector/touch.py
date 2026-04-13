"""触线检测与去抖（迁移自 algo/touch.py）。"""
from typing import List, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from timing.common.clock import Clock


def check_touch(price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    return [(r, p) for r, p in levels if abs(price - p) <= tolerance]


class TouchDetector:
    def __init__(self, cooldown_sec: float = 60.0, clock: "Clock" = None):
        self.cooldown = cooldown_sec
        self._clock = clock
        self._last: dict = {}

    def check(self, price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
        if self._clock: now = self._clock.now_sec()
        else:
            import time; now = time.time()
        touched = check_touch(price, levels, tolerance)
        out = []
        for r, p in touched:
            key = (r, p)
            if now - self._last.get(key, 0) >= self.cooldown:
                out.append((r, p))
                self._last[key] = now
        return out
