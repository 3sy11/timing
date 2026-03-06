"""Swing 拐点识别与选一笔趋势腿。"""
from dataclasses import dataclass
from typing import List, Literal, Optional

from timing.data import Kline


@dataclass
class SwingPoint:
    ts: int
    price: float
    kind: Literal['high', 'low']
    index: int  # 在 klines 中的下标


@dataclass
class TrendLeg:
    start_ts: int
    end_ts: int
    low: float
    high: float
    direction: Literal['up', 'down']
    start_index: int
    end_index: int


def find_swing_highs_lows(klines: List[Kline], left_bars: int = 5, right_bars: int = 5) -> List[SwingPoint]:
    """在 K 线序列上找 swing high/low：某根 K 的 high 在左右各 N 根内为最高、low 为最低。返回按 index 升序的拐点列表。"""
    n = len(klines)
    out: List[SwingPoint] = []
    for i in range(n):
        left = max(0, i - left_bars)
        right = min(n, i + right_bars + 1)
        seg_high = [klines[j].high for j in range(left, right)]
        seg_low = [klines[j].low for j in range(left, right)]
        if klines[i].high >= max(seg_high): out.append(SwingPoint(klines[i].ts, klines[i].high, 'high', i))
        if klines[i].low <= min(seg_low): out.append(SwingPoint(klines[i].ts, klines[i].low, 'low', i))
    return sorted(out, key=lambda s: s.index)


def select_trend_leg(swings: List[SwingPoint], direction: Literal['up', 'down', 'latest'] = 'latest', klines: Optional[List[Kline]] = None) -> Optional[TrendLeg]:
    """从拐点序列选一笔腿。up=最近一笔上涨(low->high)，down=最近一笔下跌(high->low)，latest=取最后一个拐点所在的那一笔（由前一个异类拐点构成）。无 klines 时用 swings 的 ts/price 推断 high/low。"""
    if not swings: return None
    if klines is None: klines = []
    legs: List[TrendLeg] = []
    i = 0
    while i < len(swings) - 1:
        a, b = swings[i], swings[i + 1]
        if a.kind == 'low' and b.kind == 'high':
            low, high = a.price, b.price
            if high > low: legs.append(TrendLeg(a.ts, b.ts, low, high, 'up', a.index, b.index))
        elif a.kind == 'high' and b.kind == 'low':
            high, low = a.price, b.price
            if high > low: legs.append(TrendLeg(a.ts, b.ts, low, high, 'down', a.index, b.index))
        i += 1
    if not legs: return None
    if direction == 'up': cand = [lg for lg in legs if lg.direction == 'up']
    elif direction == 'down': cand = [lg for lg in legs if lg.direction == 'down']
    else: cand = legs
    return cand[-1] if cand else None
