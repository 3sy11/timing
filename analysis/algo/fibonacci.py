"""斐波那契回撤线。"""
from typing import List, Tuple

from timing.models import Kline

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def compute_retracement_levels(high: float, low: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    span = high - low
    return [(r, low + span * r) for r in ratios]


def retracement_from_leg(leg_low: float, leg_high: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    return compute_retracement_levels(leg_high, leg_low, ratios)


def retracement_from_klines(klines: List[Kline], ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    if not klines:
        return []
    h = max(k.high for k in klines)
    l = min(k.low for k in klines)
    return compute_retracement_levels(h, l, ratios)
