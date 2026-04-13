"""DetectorService commands：FeedPrice。"""
from typing import Any, ClassVar, List, Tuple
from pydantic import Field
from bollydog.globals import hub
from bollydog.models.base import BaseCommand
from .touch import check_touch


class FeedPrice(BaseCommand):
    """显式喂价测试触线。"""
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
        return out
