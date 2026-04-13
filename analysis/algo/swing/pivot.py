"""N-bar Pivot 拐点标记。列式输出：每个 (left,right) 窗口生成 high/low 两列。
值为拐点价格或 NaN。"""
import math
from typing import Dict, List, Tuple


def tag_pivots(klines: List[dict], windows: List[Tuple[int, int]]) -> Dict[str, List[float]]:
    n = len(klines)
    tags: Dict[str, List[float]] = {}
    for left_bars, right_bars in windows:
        w = min(left_bars, right_bars)
        col_h, col_l = f"pivot_high_{w}", f"pivot_low_{w}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        for i in range(n):
            lo_idx, hi_idx = max(0, i - left_bars), min(n, i + right_bars + 1)
            seg_h = [klines[j]["high"] for j in range(lo_idx, hi_idx)]
            seg_l = [klines[j]["low"] for j in range(lo_idx, hi_idx)]
            if klines[i]["high"] >= max(seg_h): arr_h[i] = klines[i]["high"]
            if klines[i]["low"] <= min(seg_l): arr_l[i] = klines[i]["low"]
        tags[col_h] = arr_h
        tags[col_l] = arr_l
    return tags
