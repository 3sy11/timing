"""Fib 拟合：遍历所有 (H,L) 组合搜索最优 + 评分。"""
from typing import List, Optional
from timing.analysis.config import FibConfig
from timing.analysis.types import FibResult, PriceCluster
from .fibonacci import retracement_from_leg


def fit_fib(clusters_high: List[PriceCluster], clusters_low: List[PriceCluster],
            config: FibConfig) -> Optional[FibResult]:
    all_clusters = clusters_high + clusters_low
    if len(all_clusters) < 2: return None
    prices = sorted(set(c.center for c in all_clusters))
    if len(prices) < 2: return None
    price_range = prices[-1] - prices[0]
    min_span = price_range * config.min_span_pct
    cluster_map = {round(c.center, 6): c for c in all_clusters}
    best_score, best_h, best_l = 0.0, 0.0, 0.0
    for i in range(len(prices)):
        for j in range(i + 1, len(prices)):
            L, H = prices[i], prices[j]
            span = H - L
            if span < min_span: continue
            score = 0.0
            for c in all_clusters:
                if abs(c.center - H) < 1e-9 or abs(c.center - L) < 1e-9:
                    score += c.total_conf; continue
                ratio = (c.center - L) / span
                nearest = min(config.std_ratios, key=lambda r: abs(r - ratio))
                error = abs(ratio - nearest)
                if error < config.max_ratio_error:
                    score += c.total_conf * (1 - error / config.max_ratio_error)
            if score > best_score:
                best_score, best_h, best_l = score, H, L
    if best_score <= 0: return None
    levels = retracement_from_leg(best_l, best_h, config.std_ratios)
    return FibResult(best_h=best_h, best_l=best_l, levels=levels, score=best_score)
