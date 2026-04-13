"""斐波那契回撤线计算（迁移自 algo/fibonacci.py）。"""
from typing import List, Tuple
from timing.analysis.config import DEFAULT_RATIOS


def compute_retracement_levels(high: float, low: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    span = high - low
    return [(r, low + span * r) for r in ratios]


def retracement_from_leg(leg_low: float, leg_high: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    return compute_retracement_levels(leg_high, leg_low, ratios)


def retracement_from_klines(klines: List[dict], ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    if not klines: return []
    h = max(k["high"] for k in klines)
    lo = min(k["low"] for k in klines)
    return compute_retracement_levels(h, lo, ratios)
