"""价格聚类。排序滑动合并，加权均值中心。"""
from typing import List, Literal
from timing.analysis.types import PriceCluster


def cluster_prices(klines: List[dict], conf: List[float], kind: Literal["high", "low"],
                   tolerance_pct: float = 0.005, min_conf: float = 0.3) -> List[PriceCluster]:
    price_key = "high" if kind == "high" else "low"
    points = [(klines[i][price_key], conf[i], i) for i in range(len(klines)) if conf[i] >= min_conf]
    if not points: return []
    points.sort(key=lambda x: x[0])
    price_range = points[-1][0] - points[0][0]
    tol = price_range * tolerance_pct if price_range > 0 else 1.0
    clusters: List[List] = [[points[0]]]
    for p, c, idx in points[1:]:
        cluster_center = sum(pp * cc for pp, cc, _ in clusters[-1]) / sum(cc for _, cc, _ in clusters[-1])
        if abs(p - cluster_center) <= tol:
            clusters[-1].append((p, c, idx))
        else:
            clusters.append([(p, c, idx)])
    result = []
    for cl in clusters:
        total_conf = sum(c for _, c, _ in cl)
        center = sum(p * c for p, c, _ in cl) / total_conf
        last_idx = max(idx for _, _, idx in cl)
        result.append(PriceCluster(center=round(center, 6), hit_count=len(cl), total_conf=round(total_conf, 4), last_index=last_idx, kind=kind))
    return result
