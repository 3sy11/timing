"""斐波那契回撤线：基于一笔 (low, high) 或 K 线区间极值。"""
from typing import List, Tuple

from timing.data import Kline

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def compute_retracement_levels(high: float, low: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    """level = low + (high - low) * ratio，返回 [(ratio, price), ...]。"""
    span = high - low
    return [(r, low + span * r) for r in ratios]


def retracement_from_leg(leg_low: float, leg_high: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    """基于一笔腿的 low/high 计算回撤线。"""
    return compute_retracement_levels(leg_high, leg_low, ratios)


def retracement_from_klines(klines: List[Kline], ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    """区间极值法：区间内 max(high)、min(low) 作为 H/L。"""
    if not klines: return []
    h = max(k.high for k in klines)
    l = min(k.low for k in klines)
    return compute_retracement_levels(h, l, ratios)
