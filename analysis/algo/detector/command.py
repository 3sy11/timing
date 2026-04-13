"""触线检测：check_touch + TouchDetector + FeedPrice 命令。
纯函数独立可调，Command 编排 + CSV 落盘。
Jupyter: touched = check_touch(price, levels, tolerance)
"""
import time as _time
from typing import Any, ClassVar, List, Tuple, TYPE_CHECKING
from pydantic import Field
from bollydog.globals import hub, protocol
from bollydog.models.base import BaseCommand
from timing.analysis.algo import dump_csv
if TYPE_CHECKING:
    from timing.common.clock import Clock

# ── 纯函数：check_touch ──
def check_touch(price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    return [(r, p) for r, p in levels if abs(price - p) <= tolerance]

# ── 状态类：TouchDetector（带 cooldown 去抖）──
class TouchDetector:
    def __init__(self, cooldown_sec: float = 60.0, clock: "Clock" = None):
        self.cooldown = cooldown_sec
        self._clock = clock
        self._last: dict = {}

    def check(self, price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
        now = self._clock.now_sec() if self._clock else _time.time()
        touched = check_touch(price, levels, tolerance)
        out = []
        for r, p in touched:
            key = (r, p)
            if now - self._last.get(key, 0) >= self.cooldown:
                out.append((r, p)); self._last[key] = now
        return out

# ── Command ──
class FeedPrice(BaseCommand):
    """显式喂价测试触线 + CSV 落盘。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.FeedPrice"
    symbol: str = ""
    price: float = 0.0
    levels: List[Tuple[float, float]] = Field(default_factory=list)
    tolerance: float = 0.5

    async def __call__(self, *args, **kwargs) -> Any:
        touched = check_touch(self.price, list(self.levels), self.tolerance)
        out = []
        for r, p in touched:
            from timing.analysis.models import FibLevelTouched
            await hub.emit(FibLevelTouched(symbol=self.symbol, ratio=r, level_price=p, touch_price=self.price))
            out.append((r, p))
        if self.symbol and protocol and out:
            try:
                rows = [(r, p, self.price) for r, p in out]
                dump_csv(f"tmp/{self.symbol}_touch.csv", ["ratio", "level_price", "touch_price"], rows)
            except Exception: pass
        return out
