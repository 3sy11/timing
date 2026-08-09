"""zones — 从活跃 Fib 组派生价格带（zones）。

每当 Fib 组集合变化时，收集所有活跃组的 levels，按价位聚类合并为 zones。
zones 是 fib.parquet 的衍生数据，与 fib 生命周期同步。
"""
import json
from typing import List


def compute_zones(active_groups: list[dict], cluster_tol_pct: float, effective_ts: int) -> list[dict]:
    """从活跃 Fib 组的所有 levels 聚类为价格带。

    算法: 单链接贪心
    1. 收集所有活跃组的 (level_price, multiplier, direction, ratio, group_score)
    2. 按 level_price 排序
    3. 相邻 |p1 - p2| / min(p1, p2) <= cluster_tol_pct 则合并
    4. 每簇输出一条 zone
    """
    all_lines: List[dict] = []
    for g in active_groups:
        levels = json.loads(g["levels_json"]) if isinstance(g["levels_json"], str) else g["levels_json"]
        for ratio, price in levels:
            all_lines.append({
                "level_price": price,
                "multiplier": g["multiplier"],
                "direction": g["direction"],
                "ratio": ratio,
                "group_score": g["score"],
            })

    if not all_lines:
        return []

    all_lines.sort(key=lambda x: x["level_price"])

    clusters: list[list[dict]] = [[all_lines[0]]]
    for i in range(1, len(all_lines)):
        prev_price = clusters[-1][-1]["level_price"]
        cur_price = all_lines[i]["level_price"]
        if prev_price > 0 and abs(cur_price - prev_price) / prev_price <= cluster_tol_pct:
            clusters[-1].append(all_lines[i])
        else:
            clusters.append([all_lines[i]])

    zones = []
    for zone_id, cluster in enumerate(clusters):
        prices = [l["level_price"] for l in cluster]
        scores = [l["group_score"] for l in cluster]
        total_score = sum(scores)

        zone_price = sum(p * s for p, s in zip(prices, scores)) / total_score if total_score > 0 else sum(prices) / len(prices)
        zone_low = min(prices)
        zone_high = max(prices)

        unique_groups = set((l["multiplier"], l["direction"]) for l in cluster)
        lines_detail = [{"m": l["multiplier"], "d": l["direction"], "r": l["ratio"], "p": round(l["level_price"], 4)} for l in cluster]

        zones.append({
            "effective_ts": effective_ts,
            "invalidated_ts": None,
            "zone_id": zone_id,
            "zone_price": round(zone_price, 4),
            "zone_low": round(zone_low, 4),
            "zone_high": round(zone_high, 4),
            "zone_width": round(zone_high - zone_low, 4),
            "n_lines": len(cluster),
            "consensus": len(unique_groups),
            "strength": round(total_score, 4),
            "lines_json": json.dumps(lines_detail),
        })

    return zones
