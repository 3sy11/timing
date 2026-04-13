"""回归偏差拐点标记。滑动窗口线性回归，偏差超 2 倍标准差为拐点。
值为拐点价格或 NaN。"""
import math
from typing import Dict, List


def _linreg_residuals(values: List[float]) -> List[float]:
    n = len(values)
    if n < 3: return [0.0] * n
    sx = n * (n - 1) / 2
    sx2 = n * (n - 1) * (2 * n - 1) / 6
    sy = sum(values)
    sxy = sum(i * v for i, v in enumerate(values))
    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-12: return [0.0] * n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return [values[i] - (intercept + slope * i) for i in range(n)]


def tag_regression(klines: List[dict], windows: List[int]) -> Dict[str, List[float]]:
    n = len(klines)
    tags: Dict[str, List[float]] = {}
    for w in windows:
        col_h, col_l = f"reg_high_{w}", f"reg_low_{w}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        if n < w:
            tags[col_h] = arr_h; tags[col_l] = arr_l; continue
        for i in range(w - 1, n):
            seg = [klines[j]["close"] for j in range(i - w + 1, i + 1)]
            residuals = _linreg_residuals(seg)
            std = math.sqrt(sum(r * r for r in residuals) / len(residuals)) if residuals else 0
            if std < 1e-12: continue
            r_last = residuals[-1]
            if r_last > 2 * std: arr_h[i] = klines[i]["high"]
            elif r_last < -2 * std: arr_l[i] = klines[i]["low"]
        tags[col_h] = arr_h; tags[col_l] = arr_l
    return tags
