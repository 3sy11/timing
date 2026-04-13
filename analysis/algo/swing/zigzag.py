"""ZigZag 拐点标记。百分比阈值过滤小波动，保留大转折。
值为拐点价格或 NaN。"""
import math
from typing import Dict, List


def tag_zigzag(klines: List[dict], thresholds: List[float]) -> Dict[str, List[float]]:
    n = len(klines)
    tags: Dict[str, List[float]] = {}
    for thr in thresholds:
        pct = int(thr * 100)
        col_h, col_l = f"zigzag_high_{pct}", f"zigzag_low_{pct}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        if n == 0:
            tags[col_h] = arr_h; tags[col_l] = arr_l; continue
        state = "init"
        last_hi, last_lo = klines[0]["high"], klines[0]["low"]
        last_hi_idx, last_lo_idx = 0, 0
        for i in range(n):
            hi, lo = klines[i]["high"], klines[i]["low"]
            if state == "init":
                if hi > last_hi: last_hi, last_hi_idx = hi, i
                if lo < last_lo: last_lo, last_lo_idx = lo, i
                if last_hi > 0 and (last_hi - last_lo) / last_hi >= thr:
                    if last_hi_idx > last_lo_idx:
                        arr_l[last_lo_idx] = last_lo; state = "up"; last_hi, last_hi_idx = hi, i
                    else:
                        arr_h[last_hi_idx] = last_hi; state = "down"; last_lo, last_lo_idx = lo, i
            elif state == "up":
                if hi > last_hi: last_hi, last_hi_idx = hi, i
                if last_hi > 0 and (last_hi - lo) / last_hi >= thr:
                    arr_h[last_hi_idx] = last_hi; state = "down"; last_lo, last_lo_idx = lo, i
            elif state == "down":
                if lo < last_lo: last_lo, last_lo_idx = lo, i
                if last_lo > 0 and (hi - last_lo) / last_lo >= thr:
                    arr_l[last_lo_idx] = last_lo; state = "up"; last_hi, last_hi_idx = hi, i
        if state == "up": arr_h[last_hi_idx] = last_hi
        elif state == "down": arr_l[last_lo_idx] = last_lo
        tags[col_h] = arr_h; tags[col_l] = arr_l
    return tags
