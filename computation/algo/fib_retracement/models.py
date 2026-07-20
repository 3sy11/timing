from dataclasses import dataclass
from typing import List, Literal, Tuple


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
    multiplier: int = 0

    @property
    def best_h(self) -> float: return self.leg.high
    @property
    def best_l(self) -> float: return self.leg.low
