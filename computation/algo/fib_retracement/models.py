from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple

# ═══════════════════════════════════════════════════
#  统一 Line 数据结构 (替代 PriceLine + FibLevel + FibResult)
# ═══════════════════════════════════════════════════

LINE_TYPES = ("detected", "fib_anchored", "fib_inferred", "fib_extended")


@dataclass
class Line:
    """统一的价格线: detected / fib_anchored / fib_inferred / fib_extended。"""
    compute_ts: int = 0
    compute_bar_idx: int = 0
    multiplier: int = 0
    type: str = "detected"  # detected / fib_anchored / fib_inferred / fib_extended
    center: float = 0.0
    # detected 专属
    hit_count: int = 0
    total_conf: float = 0.0
    time_span_ratio: float = 0.0
    has_high: bool = False
    has_low: bool = False
    is_bidirectional: bool = False
    strength: float = 0.0
    tolerance: float = 0.0
    # fib 专属
    fib_ratio: Optional[float] = None
    fib_quality: float = 0.0
    fib_leg_high: float = 0.0
    fib_leg_low: float = 0.0
    # 锚定引用: fib_price 与最近 detected 线的差值 (正=fib在上, 负=fib在下)
    anchor_center: Optional[float] = None


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


# ═══════════════════════════════════════════════════
#  v3 数据结构
# ═══════════════════════════════════════════════════

@dataclass
class PriceLine:
    """v3: 价格线 — Stage 3 聚合产出的第一公民。"""
    center: float
    tolerance: float
    hit_count: int
    total_conf: float
    time_span_ratio: float = 0.0
    has_high: bool = False
    has_low: bool = False
    is_bidirectional: bool = False
    line_strength: float = 0.0
    first_touch_ts: int = 0
    last_touch_ts: int = 0
    first_touch_idx: int = 0
    last_touch_idx: int = 0


@dataclass
class FibLevel:
    """v3: 单条 Fib 水平线，含锚点追溯。"""
    ratio: float
    price: float
    is_anchored: bool = False
    anchor_center: Optional[float] = None
    anchor_strength: float = 0.0


@dataclass
class FibResult:
    """v3: Stage 4 完整输出。"""
    is_valid: bool = False
    fib_quality: float = 0.0
    leg_high: float = 0.0
    leg_low: float = 0.0
    levels: List[FibLevel] = field(default_factory=list)
    price_lines: List[PriceLine] = field(default_factory=list)


# ═══ 兼容旧代码的 DensityBand (v2, deprecated) ═══
@dataclass
class DensityBand:
    center: float
    band_low: float
    band_high: float
    hit_count: int
    total_conf: float
    first_idx: int = 0
    last_idx: int = 0
    first_ts: int = 0
    last_ts: int = 0
    has_high: bool = False
    has_low: bool = False
    is_bidirectional: bool = False
    time_span_ratio: float = 0.0
    band_strength: float = 0.0
    fib_ratio: Optional[float] = None
    fib_direction: Optional[str] = None
