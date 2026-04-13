"""分析域数据类型，与共享 Kline 分离。"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple


@dataclass
class SwingPoint:
    ts: int
    price: float
    kind: Literal["high", "low"]
    index: int


@dataclass
class PriceCluster:
    center: float
    hit_count: int
    total_conf: float
    last_index: int
    kind: Literal["high", "low"]


@dataclass
class FibResult:
    best_h: float
    best_l: float
    levels: List[Tuple[float, float]]
    score: float
    leg_start_ts: int = 0
    leg_end_ts: int = 0
    direction: str = "latest"


@dataclass
class FibLevel:
    ratio: float
    price: float
    direction_hint: Literal["UP", "DOWN", "NEUTRAL"] = "NEUTRAL"
    symbol: str = ""
    interval: str = ""
    computed_at_revision: int = 0


@dataclass
class TouchResult:
    ratio: float
    level_price: float
    touch_price: float
    direction: Literal["UP_TO_DOWN", "DOWN_TO_UP", "UNKNOWN"] = "UNKNOWN"
    symbol: str = ""
    interval: str = ""


@dataclass
class TrendLeg:
    start_ts: int
    end_ts: int
    low: float
    high: float
    direction: Literal["up", "down"]
    start_index: int
    end_index: int
