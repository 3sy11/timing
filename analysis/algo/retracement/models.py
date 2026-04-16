from dataclasses import dataclass
from typing import ClassVar, List, Literal, Tuple
from pydantic import Field
from bollydog.models.base import BaseEvent


@dataclass
class TrendLeg:
    """从拐点序列中提取的一条趋势腿。"""
    start_idx: int; end_idx: int
    start_ts: int; end_ts: int
    low: float; high: float
    direction: Literal["up", "down"]
    span_pct: float = 0.0
    conf_score: float = 0.0


@dataclass
class FibGroup:
    """一组 Fib 回撤线，绑定在一条趋势腿上。"""
    leg: TrendLeg
    levels: List[Tuple[float, float]]  # [(ratio, price), ...]
    score: float = 0.0
    direction: Literal["up", "down"] = "up"

    @property
    def best_h(self) -> float: return self.leg.high
    @property
    def best_l(self) -> float: return self.leg.low


# ── 事件：由 command 广播，策略层消费 ──

class FibLevelTouched(BaseEvent):
    """某条 fib 线被当前价格触碰。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.FibLevelTouched"
    symbol: str = ""; ratio: float = 0.0; level_price: float = 0.0; touch_price: float = 0.0
    async def __call__(self, *args, **kwargs): return await super().__call__(*args, **kwargs)


class FibInvalidated(BaseEvent):
    """某组 fib 因价格突破趋势腿边界而失效，需要重算。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.FibInvalidated"
    symbol: str = ""; interval: str = ""; group_idx: int = 0
    direction: str = ""; break_side: str = ""; close: float = 0.0
    async def __call__(self, *args, **kwargs): return await super().__call__(*args, **kwargs)
