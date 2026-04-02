"""Swing 拐点识别与选一笔趋势腿。"""
from dataclasses import dataclass
from typing import List, Literal, Optional


@dataclass
class SwingPoint:
    ts: int
    price: float
    kind: Literal["high", "low"]
    index: int


@dataclass
class TrendLeg:
    start_ts: int
    end_ts: int
    low: float
    high: float
    direction: Literal["up", "down"]
    start_index: int
    end_index: int


def find_swing_highs_lows(klines: List[dict], left_bars: int = 5, right_bars: int = 5) -> List[SwingPoint]:
    n = len(klines)
    out: List[SwingPoint] = []
    for i in range(n):
        left = max(0, i - left_bars)
        right = min(n, i + right_bars + 1)
        seg_high = [klines[j]["high"] for j in range(left, right)]
        seg_low = [klines[j]["low"] for j in range(left, right)]
        if klines[i]["high"] >= max(seg_high):
            out.append(SwingPoint(klines[i]["ts"], klines[i]["high"], "high", i))
        if klines[i]["low"] <= min(seg_low):
            out.append(SwingPoint(klines[i]["ts"], klines[i]["low"], "low", i))
    return sorted(out, key=lambda s: s.index)


def select_trend_leg(
    swings: List[SwingPoint], direction: Literal["up", "down", "latest"] = "latest", klines: Optional[List[dict]] = None
) -> Optional[TrendLeg]:
    if not swings:
        return None
    legs: List[TrendLeg] = []
    i = 0
    while i < len(swings) - 1:
        a, b = swings[i], swings[i + 1]
        if a.kind == "low" and b.kind == "high":
            if b.price > a.price:
                legs.append(TrendLeg(a.ts, b.ts, a.price, b.price, "up", a.index, b.index))
        elif a.kind == "high" and b.kind == "low":
            if a.price > b.price:
                legs.append(TrendLeg(a.ts, b.ts, b.price, a.price, "down", a.index, b.index))
        i += 1
    if not legs:
        return None
    if direction == "up":
        cand = [lg for lg in legs if lg.direction == "up"]
    elif direction == "down":
        cand = [lg for lg in legs if lg.direction == "down"]
    else:
        cand = legs
    return cand[-1] if cand else None
