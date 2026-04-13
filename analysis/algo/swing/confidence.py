"""置信度计算。加权汇总各算法标记列，归一化到 [0,1]。"""
import math
from typing import Dict, List, Tuple


def compute_confidence(tags: Dict[str, List[float]], weights: Dict[str, float]) -> Tuple[List[float], List[float]]:
    if not tags: return [], []
    n = len(next(iter(tags.values())))
    conf_high, conf_low = [0.0] * n, [0.0] * n
    max_w = sum(weights.values()) or 1.0
    for col, arr in tags.items():
        parts = col.rsplit("_", 1)
        base = parts[0] if len(parts) == 2 else col
        w_key_candidates = [col.replace("_high", "").replace("_low", "")]
        for cand in w_key_candidates:
            w = weights.get(cand, 0)
            if w: break
        else:
            w = 0.5
        is_high = "_high" in col
        for i in range(n):
            if not math.isnan(arr[i]):
                if is_high: conf_high[i] += w
                else: conf_low[i] += w
    for i in range(n):
        conf_high[i] = min(conf_high[i] / max_w, 1.0)
        conf_low[i] = min(conf_low[i] / max_w, 1.0)
    return conf_high, conf_low
