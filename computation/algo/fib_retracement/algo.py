"""fib_retracement 纯函数：swing 拐点识别 → 聚类 → 趋势腿提取 → Fib 回撤。

所有函数不依赖 app/hub，可在 notebook / 测试中直接调用。
"""
import logging, math
from typing import Dict, List, Literal, Optional, Tuple
import pandas as pd
from .config import RetracementConfig
from .models import TrendLeg, FibGroup, DensityBand, PriceLine, FibLevel, FibResult, Line

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
#  第一阶段：swing 拐点识别
# ═══════════════════════════════════════════════════

def base_df(klines: List[dict]) -> pd.DataFrame:
    if not klines: return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(klines).copy()
    for c in ("ts", "open", "high", "low", "close"):
        if c not in df.columns: raise ValueError(f"missing kline column: {c}")
    if "volume" not in df.columns: df["volume"] = 0.0
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce").astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df.sort_values("ts").reset_index(drop=True)


def tag_pivots(df: pd.DataFrame, windows: List[Tuple[int, int]]) -> Tuple[pd.DataFrame, Dict[str, str]]:
    out, wmap, n = df.copy(), {}, len(df)
    highs, lows = out["high"].tolist(), out["low"].tolist()
    for left_bars, right_bars in windows:
        key = f"pivot_{min(left_bars, right_bars)}"
        col_h, col_l = f"pivot_high_{left_bars}x{right_bars}", f"pivot_low_{left_bars}x{right_bars}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        for i in range(n):
            lo_idx, hi_idx = max(0, i - left_bars), min(n, i + right_bars + 1)
            seg_h, seg_l = highs[lo_idx:hi_idx], lows[lo_idx:hi_idx]
            if seg_h and highs[i] >= max(seg_h): arr_h[i] = highs[i]
            if seg_l and lows[i] <= min(seg_l): arr_l[i] = lows[i]
        out[col_h], out[col_l] = arr_h, arr_l
        wmap[col_h], wmap[col_l] = key, key
    return out, wmap


def tag_zigzag(df: pd.DataFrame, thresholds: List[float]) -> Tuple[pd.DataFrame, Dict[str, str]]:
    out, wmap, n = df.copy(), {}, len(df)
    highs, lows = out["high"].tolist(), out["low"].tolist()
    for thr in thresholds:
        pct = int(thr * 100)
        key, col_h, col_l = f"zigzag_{pct}", f"zigzag_high_{pct}", f"zigzag_low_{pct}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        if n == 0:
            out[col_h], out[col_l] = arr_h, arr_l; wmap[col_h], wmap[col_l] = key, key; continue
        state, last_hi, last_lo = "init", highs[0], lows[0]
        last_hi_idx, last_lo_idx = 0, 0
        for i in range(n):
            hi, lo = highs[i], lows[i]
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
        out[col_h], out[col_l] = arr_h, arr_l
        wmap[col_h], wmap[col_l] = key, key
    return out, wmap


def _linreg_residuals(values: List[float]) -> List[float]:
    n = len(values)
    if n < 3: return [0.0] * n
    sx, sx2 = n * (n - 1) / 2, n * (n - 1) * (2 * n - 1) / 6
    sy = sum(values); sxy = sum(i * v for i, v in enumerate(values))
    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-12: return [0.0] * n
    slope = (n * sxy - sx * sy) / denom; intercept = (sy - slope * sx) / n
    return [values[i] - (intercept + slope * i) for i in range(n)]


def tag_regression(df: pd.DataFrame, windows: List[int]) -> Tuple[pd.DataFrame, Dict[str, str]]:
    out, wmap, n = df.copy(), {}, len(df)
    closes, highs, lows = out["close"].tolist(), out["high"].tolist(), out["low"].tolist()
    for w in windows:
        key, col_h, col_l = f"reg_{w}", f"reg_high_{w}", f"reg_low_{w}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        if n < w:
            out[col_h], out[col_l] = arr_h, arr_l; wmap[col_h], wmap[col_l] = key, key; continue
        for i in range(w - 1, n):
            seg = closes[i - w + 1:i + 1]
            residuals = _linreg_residuals(seg)
            std = math.sqrt(sum(r * r for r in residuals) / len(residuals)) if residuals else 0
            if std < 1e-12: continue
            if residuals[-1] > 2 * std: arr_h[i] = highs[i]
            elif residuals[-1] < -2 * std: arr_l[i] = lows[i]
        out[col_h], out[col_l] = arr_h, arr_l
        wmap[col_h], wmap[col_l] = key, key
    return out, wmap


def compute_confidence(df: pd.DataFrame, wmap: Dict[str, str], weights: Dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    conf_h, conf_l, max_w = pd.Series(0.0, index=out.index), pd.Series(0.0, index=out.index), (sum(weights.values()) or 1.0)
    for col, key in wmap.items():
        if col not in out.columns: continue
        w = weights.get(key, 0.5)
        hit = out[col].notna().astype("float64") * w
        if "_high_" in col: conf_h += hit
        elif "_low_" in col: conf_l += hit
    out["conf_high"] = (conf_h / max_w).clip(upper=1.0)
    out["conf_low"] = (conf_l / max_w).clip(upper=1.0)
    return out


def cluster_prices(df: pd.DataFrame, kind: Literal["high", "low"],
                   tolerance_pct: float = 0.005, min_conf: float = 0.3) -> pd.DataFrame:
    price_key, conf_key = ("high", "conf_high") if kind == "high" else ("low", "conf_low")
    points = [(float(df.at[i, price_key]), float(df.at[i, conf_key]), i, int(df.at[i, "ts"])) for i in range(len(df)) if float(df.at[i, conf_key]) >= min_conf]
    if not points: return pd.DataFrame(columns=["kind", "center", "hit_count", "total_conf", "last_index", "last_ts"])
    points.sort(key=lambda x: x[0])
    price_range = points[-1][0] - points[0][0]
    tol = price_range * tolerance_pct if price_range > 0 else 1.0
    clusters: List[List] = [[points[0]]]
    for p, c, idx, ts in points[1:]:
        center = sum(pp * cc for pp, cc, _, _ in clusters[-1]) / sum(cc for _, cc, _, _ in clusters[-1])
        if abs(p - center) <= tol: clusters[-1].append((p, c, idx, ts))
        else: clusters.append([(p, c, idx, ts)])
    result = []
    for cl in clusters:
        total_conf = sum(c for _, c, _, _ in cl)
        center = sum(p * c for p, c, _, _ in cl) / total_conf
        last_idx = max(idx for _, _, idx, _ in cl); last_ts = max(ts for _, _, _, ts in cl)
        result.append({"kind": kind, "center": round(center, 6), "hit_count": len(cl), "total_conf": round(total_conf, 4), "last_index": last_idx, "last_ts": last_ts})
    return pd.DataFrame(result)


# ═══════════════════════════════════════════════════
#  第二阶段：趋势腿提取 + Fib 回撤
# ═══════════════════════════════════════════════════

def extract_trend_legs(feature_df: pd.DataFrame, clusters_high_df: pd.DataFrame,
                       clusters_low_df: pd.DataFrame, min_span_pct: float = 0.03) -> List[TrendLeg]:
    if feature_df.empty or "conf_high" not in feature_df.columns: return []
    cluster_centers_h = set(round(float(r), 6) for r in clusters_high_df["center"]) if not clusters_high_df.empty else set()
    cluster_centers_l = set(round(float(r), 6) for r in clusters_low_df["center"]) if not clusters_low_df.empty else set()
    highs, lows = [], []
    for i in range(len(feature_df)):
        row = feature_df.iloc[i]
        ch, cl = float(row.get("conf_high", 0)), float(row.get("conf_low", 0))
        if ch > 0: highs.append((i, int(row["ts"]), float(row["high"]), ch))
        if cl > 0: lows.append((i, int(row["ts"]), float(row["low"]), cl))
    if not highs or not lows: return []
    def _cluster_bonus(price: float, centers: set, tol_pct: float = 0.005) -> float:
        for c in centers:
            if c > 0 and abs(price - c) / c < tol_pct: return 1.0
        return 0.0
    all_points = sorted(
        [(idx, ts, price, conf, "high") for idx, ts, price, conf in highs] +
        [(idx, ts, price, conf, "low") for idx, ts, price, conf in lows],
        key=lambda x: x[0])
    legs: List[TrendLeg] = []
    for i in range(len(all_points)):
        for j in range(i + 1, len(all_points)):
            idx_a, ts_a, price_a, conf_a, kind_a = all_points[i]
            idx_b, ts_b, price_b, conf_b, kind_b = all_points[j]
            if idx_b - idx_a < 3: continue
            if kind_a == "low" and kind_b == "high" and price_b > price_a:
                low_p, high_p, direction = price_a, price_b, "up"
            elif kind_a == "high" and kind_b == "low" and price_a > price_b:
                low_p, high_p, direction = price_b, price_a, "down"
            else: continue
            span_pct = (high_p - low_p) / low_p if low_p > 0 else 0
            if span_pct < min_span_pct: continue
            bonus_a = _cluster_bonus(price_a, cluster_centers_l if kind_a == "low" else cluster_centers_h)
            bonus_b = _cluster_bonus(price_b, cluster_centers_h if kind_b == "high" else cluster_centers_l)
            conf_score = (conf_a + bonus_a) + (conf_b + bonus_b)
            legs.append(TrendLeg(start_idx=idx_a, end_idx=idx_b, start_ts=ts_a, end_ts=ts_b,
                                 low=low_p, high=high_p, direction=direction,
                                 span_pct=span_pct, conf_score=conf_score))
    return legs


def score_and_rank(legs: List[TrendLeg], top_n: int = 6, total_bars: int = None) -> List[TrendLeg]:
    if not legs: return []
    max_idx = max(lg.end_idx for lg in legs)
    for lg in legs:
        base = lg.span_pct * lg.conf_score
        recency = lg.end_idx / max_idx if max_idx > 0 else 1.0
        length_ratio = (lg.end_idx - lg.start_idx) / max_idx if max_idx > 0 else 1.0
        length_penalty = 1.0 if length_ratio < 0.6 else (1.0 - (length_ratio - 0.6) / 0.4 * 0.7)
        lg.conf_score = base * recency * length_penalty
    legs.sort(key=lambda x: x.conf_score, reverse=True)
    kept_up, kept_down = [], []
    quota_each = top_n // 2
    for lg in legs:
        if lg.direction == "up" and len(kept_up) < quota_each:
            if not any(k.start_idx <= lg.start_idx and k.end_idx >= lg.end_idx for k in kept_up):
                kept_up.append(lg)
        elif lg.direction == "down" and len(kept_down) < quota_each:
            if not any(k.start_idx <= lg.start_idx and k.end_idx >= lg.end_idx for k in kept_down):
                kept_down.append(lg)
        if len(kept_up) >= quota_each and len(kept_down) >= quota_each: break
    result = []
    for u, d in zip(kept_up, kept_down): result.extend([u, d])
    result.extend(kept_up[len(result)//2:])
    result.extend(kept_down[len(result)//2:])
    return result[:top_n]


def adaptive_window_start(feature_df: pd.DataFrame, base_bars: int, min_conf: float = 0.1) -> int:
    n = len(feature_df)
    if n <= base_bars: return 0
    naive = n - base_bars
    if "conf_high" not in feature_df.columns: return max(0, naive)
    ch_loc, cl_loc = feature_df.columns.get_loc("conf_high"), feature_df.columns.get_loc("conf_low")
    changes, prev_dir = 0, None
    for i in range(n - 1, -1, -1):
        ch, cl = float(feature_df.iat[i, ch_loc]), float(feature_df.iat[i, cl_loc])
        if ch < min_conf and cl < min_conf: continue
        d = "high" if ch >= cl else "low"
        if prev_dir is not None and d != prev_dir:
            changes += 1
            if changes >= 2: return min(naive, i)
        prev_dir = d
    return max(0, naive)


def merge_legs_weighted(legs: List[TrendLeg]) -> TrendLeg:
    if not legs: return None
    if len(legs) == 1: return legs[0]
    total_w = sum(lg.conf_score for lg in legs) or 1.0
    def wavg(attr): return sum(getattr(lg, attr) * lg.conf_score for lg in legs) / total_w
    low, high = wavg("low"), wavg("high")
    return TrendLeg(start_idx=int(round(wavg("start_idx"))), end_idx=int(round(wavg("end_idx"))),
                    start_ts=int(round(wavg("start_ts"))), end_ts=int(round(wavg("end_ts"))),
                    low=low, high=high, direction=legs[0].direction,
                    span_pct=(high - low) / low if low > 0 else 0, conf_score=total_w)


def compute_retracement_levels(leg: TrendLeg, ratios=None) -> List[Tuple[float, float]]:
    ratios = ratios or [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    span = leg.high - leg.low
    if leg.direction == "up": return [(r, leg.high - span * r) for r in ratios]
    return [(r, leg.low + span * r) for r in ratios]


def fit_fib_groups(legs: List[TrendLeg], ratios=None) -> List[FibGroup]:
    ratios = ratios or [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    return [FibGroup(leg=lg, levels=compute_retracement_levels(lg, ratios), score=lg.conf_score, direction=lg.direction) for lg in legs]


# ═══════════════════════════════════════════════════
#  自下而上: 聚类拟合 Fib 网格
# ═══════════════════════════════════════════════════

_ACTIVE_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]
_ALL_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def _solve_hl(p1: float, r1: float, p2: float, r2: float, direction: str):
    """从 2 个 (price, ratio) 解出 (high, low)。"""
    denom = r2 - r1
    if abs(denom) < 1e-10:
        return 0.0, 0.0
    if direction == "up":
        span = (p1 - p2) / denom
        high = p1 + span * r1
        low = high - span
    else:
        span = (p2 - p1) / denom
        low = p1 - span * r1
        high = low + span
    return high, low


def _score_fit(prices, confs, high, low, direction, tol_pct=0.02):
    """评估聚类对 (high,low) 网格对齐度，返回 (score, assigned_count)。
    加入 ratio 覆盖率惩罚：5条内层线都应有对应聚类。
    """
    span = high - low
    if span <= 0:
        return 0.0, 0
    assigned, score = 0, 0.0
    covered_ratios = set()
    for p, c in zip(prices, confs):
        implied_r = (high - p) / span if direction == "up" else (p - low) / span
        best_ar, best_dist = None, float('inf')
        for ar in _ACTIVE_RATIOS:
            d = abs(implied_r - ar)
            if d < best_dist:
                best_dist, best_ar = d, ar
        if best_dist <= tol_pct:
            assigned += 1
            score += c * (1.0 - best_dist / tol_pct)
            covered_ratios.add(best_ar)
    # 覆盖率惩罚: 5线中覆盖比例越高分越高
    coverage = len(covered_ratios) / len(_ACTIVE_RATIOS)
    score *= coverage
    return score, assigned


def fit_fib_grid_to_clusters(cluster_centers: List[Tuple[float, float]],
                             direction: str, min_span_pct: float = 0.03,
                             min_assigned: int = 3) -> List[dict]:
    """从聚类中心搜索最佳 Fib 网格拟合。
    Args:
        cluster_centers: [(price, total_conf), ...] 按 price 排序
        direction: "up" 或 "down"
        min_span_pct: leg 最小幅度占比
        min_assigned: 最少对齐聚类数
    Returns:
        [{high, low, score, assigned}, ...] 按 score 降序, 最多 2 条
    """
    n = len(cluster_centers)
    if n < 3:
        return []
    prices = [c[0] for c in cluster_centers]
    confs = [c[1] for c in cluster_centers]
    price_min, price_max = min(prices), max(prices)
    price_range = price_max - price_min if price_max > price_min else 1.0
    seen, results = set(), []
    for i in range(n):
        for j in range(i + 1, n):
            for ri in range(4):
                for rj in range(ri + 1, 5):
                    # up方向: 高价对应低ratio, 低价对应高ratio
                    if direction == "up":
                        h, l = _solve_hl(prices[j], _ACTIVE_RATIOS[ri],
                                         prices[i], _ACTIVE_RATIOS[rj], direction)
                    else:
                        h, l = _solve_hl(prices[i], _ACTIVE_RATIOS[ri],
                                         prices[j], _ACTIVE_RATIOS[rj], direction)
                    if h <= l or l <= 0:
                        continue
                    if (h - l) / l < min_span_pct:
                        continue
                    # 约束: 0%/100%外推不超过聚类价格范围的1.5倍
                    if h > price_max + price_range * 1.5 or l < price_min - price_range * 1.5:
                        continue
                    key = (round(h, 2), round(l, 2))
                    if key in seen:
                        continue
                    seen.add(key)
                    score, assigned = _score_fit(prices, confs, h, l, direction)
                    if assigned >= min_assigned:
                        results.append({"high": h, "low": l, "score": score, "assigned": assigned})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:2]


def levels_from_hl(high: float, low: float, direction: str) -> List[Tuple[float, float]]:
    """从 high/low 生成 7 条 Fib level。"""
    span = high - low
    if direction == "up":
        return [(r, high - span * r) for r in _ALL_RATIOS]
    return [(r, low + span * r) for r in _ALL_RATIOS]


# ═══════════════════════════════════════════════════
#  v3 Stage 3：价格线聚合
# ═══════════════════════════════════════════════════

def aggregate_price_lines(feature_df: pd.DataFrame, cfg) -> List[PriceLine]:
    """v3 Stage 3: 从 conf_high/conf_low 聚合出 N 条价格线。
    high/low 拐点全部合并到一个列表，不区分来源。
    tolerance = price_range × cluster_tolerance_pct（相对于整个价格区间）。
    """
    n = len(feature_df)
    min_conf = cfg.min_cluster_conf
    # 第一步: 收集候选拐点 (price, conf, idx, ts, kind)
    candidates = []
    for i in range(n):
        ch = float(feature_df.iat[i, feature_df.columns.get_loc("conf_high")])
        cl = float(feature_df.iat[i, feature_df.columns.get_loc("conf_low")])
        ts = int(feature_df.iat[i, feature_df.columns.get_loc("ts")])
        if ch >= min_conf:
            candidates.append((float(feature_df.iat[i, feature_df.columns.get_loc("high")]), ch, i, ts, "high"))
        if cl >= min_conf:
            candidates.append((float(feature_df.iat[i, feature_df.columns.get_loc("low")]), cl, i, ts, "low"))
    if not candidates:
        return []
    # 第二步: 计算 tolerance, 按价格排序, 滑动合并
    prices = [c[0] for c in candidates]
    price_range = max(prices) - min(prices)
    if price_range <= 0:
        return []
    tol = price_range * cfg.cluster_tolerance_pct
    candidates.sort(key=lambda x: x[0])
    # 滑动合并: 以加权中心判断（避免链式漂移）
    groups = []  # each: list of (price, conf, idx, ts, kind)
    current_group = [candidates[0]]
    for c in candidates[1:]:
        # 当前组的加权中心
        total_w = sum(x[1] for x in current_group)
        center = sum(x[0] * x[1] for x in current_group) / total_w if total_w > 0 else current_group[0][0]
        if abs(c[0] - center) <= tol:
            current_group.append(c)
        else:
            groups.append(current_group)
            current_group = [c]
    groups.append(current_group)
    # 第三步: 计算每条价格线的指标
    lines = []
    for grp in groups:
        total_w = sum(x[1] for x in grp)
        center = sum(x[0] * x[1] for x in grp) / total_w if total_w > 0 else grp[0][0]
        hit_count = len(grp)
        total_conf = total_w
        idxs = [x[2] for x in grp]
        tss = [x[3] for x in grp]
        first_idx, last_idx = min(idxs), max(idxs)
        first_ts, last_ts = min(tss), max(tss)
        time_span_ratio = (last_idx - first_idx) / n if n > 0 else 0.0
        has_high = any(x[4] == "high" for x in grp)
        has_low = any(x[4] == "low" for x in grp)
        is_bidirectional = has_high and has_low
        line_strength = total_conf * (1.0 + time_span_ratio) * (1.5 if is_bidirectional else 1.0)
        lines.append(PriceLine(
            center=round(center, 4), tolerance=round(tol, 4),
            hit_count=hit_count, total_conf=round(total_conf, 4),
            time_span_ratio=round(time_span_ratio, 4),
            has_high=has_high, has_low=has_low, is_bidirectional=is_bidirectional,
            line_strength=round(line_strength, 4),
            first_touch_ts=first_ts, last_touch_ts=last_ts,
            first_touch_idx=first_idx, last_touch_idx=last_idx,
        ))
    # 第四步: 按 line_strength 排序, 过滤 + 截取 Top-N
    lines.sort(key=lambda x: x.line_strength, reverse=True)
    lines = [l for l in lines if l.line_strength >= cfg.min_line_strength]
    lines = lines[:cfg.max_price_lines]
    log.info(f'[aggregate_price_lines] candidates={len(candidates)} groups={len(groups)} '
             f'output={len(lines)} tol={tol:.2f}')
    return lines


# ═══════════════════════════════════════════════════
#  v3 Stage 4：Fib 拟合与解释层
# ═══════════════════════════════════════════════════

def fit_fib_to_price_lines(price_lines: List[PriceLine], cfg) -> FibResult:
    """v3 Stage 4: 从 price_lines 中找最优 Fib 网格。
    锚定逻辑: 两条价格线作为 ratio=0.236 和 ratio=0.786 的铆钉，
    从中反推 H(1.0) 和 L(0.0)，0%/100% 是推断结果而非锚点。
    """
    if len(price_lines) < 2:
        return FibResult(is_valid=False, price_lines=price_lines)
    std_ratios = cfg.std_ratios
    top_k = min(cfg.top_lines_for_fit, len(price_lines))
    top_lines = sorted(price_lines, key=lambda x: x.line_strength, reverse=True)[:top_k]
    # 锚定比率: 0.236 对应高锚点, 0.786 对应低锚点
    R_HIGH_ANCHOR = 0.786  # 高价格锚点对应的 ratio
    R_LOW_ANCHOR = 0.236   # 低价格锚点对应的 ratio
    ANCHOR_SPAN = R_HIGH_ANCHOR - R_LOW_ANCHOR  # 0.55
    # 第一步: 两两组合, 高价=0.786锚点, 低价=0.236锚点, 反推 (H, L)
    best_score, best_high_anchor, best_low_anchor = 0.0, None, None
    best_H, best_L = 0.0, 0.0
    for i in range(len(top_lines)):
        for j in range(i + 1, len(top_lines)):
            hi = top_lines[i] if top_lines[i].center > top_lines[j].center else top_lines[j]
            lo = top_lines[j] if top_lines[i].center > top_lines[j].center else top_lines[i]
            # hi → ratio=0.786 锚点, lo → ratio=0.236 锚点
            anchor_span = hi.center - lo.center
            if anchor_span <= 0:
                continue
            full_span = anchor_span / ANCHOR_SPAN
            L = lo.center - R_LOW_ANCHOR * full_span
            H = L + full_span
            if L <= 0 or full_span / L < cfg.min_leg_span_pct:
                continue
            # 第二步: 对齐评分 (只考虑内层ratio, 0和1是数学推导不参与)
            inner_ratios = [r for r in std_ratios if 0.001 < r < 0.999]
            score = hi.line_strength + lo.line_strength
            for pl in price_lines:
                if pl is hi or pl is lo:
                    continue
                ratio = (pl.center - L) / full_span
                if ratio < -0.05 or ratio > 1.05:
                    continue
                nearest_std = min(inner_ratios, key=lambda r: abs(r - ratio))
                error = abs(ratio - nearest_std)
                if error <= cfg.max_ratio_error:
                    score += pl.line_strength * (1.0 - error / cfg.max_ratio_error)
            if score > best_score:
                best_score = score
                best_high_anchor, best_low_anchor = hi, lo
                best_H, best_L = H, L
    if best_high_anchor is None:
        return FibResult(is_valid=False, price_lines=price_lines)
    # 第三步: fib_quality
    max_possible = sum(pl.line_strength for pl in price_lines)
    fib_quality = best_score / max_possible if max_possible > 0 else 0.0
    is_valid = fib_quality >= cfg.min_fib_quality
    # 第四步: 生成 FibLevel, 标注锚点
    H, L = best_H, best_L
    span = H - L
    levels = []
    for ratio in std_ratios:
        fib_price = L + span * ratio
        anchor = None
        # 0.236 和 0.786 是确定锚定的 (来自选中的两条线)
        if abs(ratio - R_LOW_ANCHOR) < 0.001:
            anchor = best_low_anchor
        elif abs(ratio - R_HIGH_ANCHOR) < 0.001:
            anchor = best_high_anchor
        else:
            # 其余 ratio: 直接找最近的 detected 线作为 anchor
            best_dist = float('inf')
            for pl in price_lines:
                d = abs(pl.center - fib_price)
                if d < best_dist:
                    best_dist = d
                    anchor = pl
        levels.append(FibLevel(
            ratio=round(ratio, 4), price=round(fib_price, 4),
            is_anchored=anchor is not None,
            anchor_center=round(anchor.center, 4) if anchor else None,
            anchor_strength=round(anchor.line_strength, 4) if anchor else 0.0,
        ))
    anchored_count = sum(1 for lv in levels if lv.is_anchored)
    log.info(f'[fit_fib_to_price_lines] anchor_hi={best_high_anchor.center:.1f}(0.786) '
             f'anchor_lo={best_low_anchor.center:.1f}(0.236) → H={H:.1f} L={L:.1f} '
             f'quality={fib_quality:.3f} valid={is_valid} anchored={anchored_count}/7')
    return FibResult(
        is_valid=is_valid, fib_quality=round(fib_quality, 4),
        leg_high=round(H, 4), leg_low=round(L, 4),
        levels=levels, price_lines=price_lines,
    )


# ═══════════════════════════════════════════════════
#  Stage 4 v2：Fib 解释层 (旧, 保留兼容)
# ═══════════════════════════════════════════════════

_STD_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def explain_with_fib(bands: List[DensityBand], top_k: int = 8,
                     max_ratio_error: float = 0.05) -> Tuple[List[DensityBand], dict]:
    """Stage 4: 从密度带中找最优 Fib 网格解释。
    Fib 是解释者: 有密度带才有标注, 找不到好的解释则 fib_ratio=None。
    返回 (标注后的bands, fib_info={direction, leg_high, leg_low, fit_score})。
    """
    if len(bands) < 3:
        return bands, {}
    # 取强度 Top-K 的带
    top_bands = sorted(bands, key=lambda b: b.band_strength, reverse=True)[:top_k]
    centers = [b.center for b in top_bands]
    best_score, best_h, best_l, best_dir = 0.0, 0.0, 0.0, None
    # 两两组合尝试作为 (H, L)
    for i in range(len(centers)):
        for j in range(len(centers)):
            if i == j:
                continue
            h, l = max(centers[i], centers[j]), min(centers[i], centers[j])
            span = h - l
            if span <= 0 or span / l < 0.01:
                continue
            for direction in ("up", "down"):
                score = 0.0
                for b in top_bands:
                    if direction == "up":
                        implied_r = (h - b.center) / span
                    else:
                        implied_r = (b.center - l) / span
                    # 找最接近的标准 ratio
                    best_match = min(_STD_RATIOS, key=lambda r: abs(implied_r - r))
                    err = abs(implied_r - best_match)
                    if err <= max_ratio_error:
                        score += b.band_strength * (1.0 - err / max_ratio_error)
                if score > best_score:
                    best_score, best_h, best_l, best_dir = score, h, l, direction
    if best_score <= 0 or best_dir is None:
        return bands, {}
    # 标注每个 band 的 fib_ratio
    span = best_h - best_l
    fib_info = {"direction": best_dir, "leg_high": round(best_h, 4),
                "leg_low": round(best_l, 4), "fit_score": round(best_score, 4)}
    for b in bands:
        if best_dir == "up":
            implied_r = (best_h - b.center) / span
        else:
            implied_r = (b.center - best_l) / span
        best_match = min(_STD_RATIOS, key=lambda r: abs(implied_r - r))
        if abs(implied_r - best_match) <= max_ratio_error:
            b.fib_ratio = best_match
            b.fib_direction = best_dir
        else:
            b.fib_ratio = None
            b.fib_direction = None
    log.info(f'[explain_with_fib] dir={best_dir} H={best_h:.1f} L={best_l:.1f} score={best_score:.2f} '
             f'labeled={sum(1 for b in bands if b.fib_ratio is not None)}/{len(bands)}')
    return bands, fib_info


# ═══════════════════════════════════════════════════
#  Stage 5 v2：密度带生命周期管理
# ═══════════════════════════════════════════════════

def run_band_lifecycle(feature_df: pd.DataFrame, cfg, bands: List[DensityBand],
                       fib_info: dict) -> pd.DataFrame:
    """Stage 5: 逐 bar 推进密度带生命周期。
    对每个活跃 band 检测: boundary_break（close 持续远离带外）。
    缓冲 = center × cluster_tolerance_pct（和聚类容差一致的相对距离）。
    """
    n = len(feature_df)
    skip_recent = cfg.skip_recent
    window = cfg.recent_bars * 3
    start_pos = max(0, n - window)
    end_pos = max(0, n - skip_recent)
    break_bars = cfg.invalidate_break_bars
    tol_pct = cfg.cluster_tolerance_pct
    closes = feature_df["close"].tolist()
    ts_list = feature_df["ts"].tolist()
    band_states = []
    for b in bands:
        # 生命周期从 band 的 last_ts（最后贡献拐点）之后开始检测
        # last_ts 对应窗口内 df 的索引，需要映射回全局索引
        activate_idx = start_pos + b.last_idx + 1
        band_states.append({
            "band": b, "active": True, "break_count": 0,
            "activate_idx": min(activate_idx, end_pos),
            "activated_ts": int(ts_list[min(activate_idx, end_pos - 1)]),
            "invalidated_ts": None, "invalidate_reason": None,
        })
    events = []
    for bi in range(start_pos, end_pos):
        close = closes[bi]
        bar_ts = int(ts_list[bi])
        for bs in band_states:
            if not bs["active"] or bi < bs["activate_idx"]:
                continue
            b = bs["band"]
            buffer = b.center * tol_pct * 3
            if close > b.band_high + buffer or close < b.band_low - buffer:
                bs["break_count"] += 1
                if bs["break_count"] >= break_bars:
                    bs["active"] = False
                    bs["invalidated_ts"] = bar_ts
                    bs["invalidate_reason"] = "boundary_break"
                    events.append({"ts": bar_ts, "center": b.center, "event": "invalidated",
                                   "reason": "boundary_break"})
            else:
                bs["break_count"] = 0
    # 输出所有 band 的最终状态
    records = []
    for bs in band_states:
        b = bs["band"]
        records.append({
            "center": b.center, "band_low": b.band_low, "band_high": b.band_high,
            "band_strength": b.band_strength, "hit_count": b.hit_count,
            "is_bidirectional": b.is_bidirectional, "has_high": b.has_high, "has_low": b.has_low,
            "time_span_ratio": b.time_span_ratio,
            "fib_ratio": b.fib_ratio, "fib_direction": b.fib_direction,
            "fib_leg_high": fib_info.get("leg_high"), "fib_leg_low": fib_info.get("leg_low"),
            "activated_ts": bs["activated_ts"],
            "invalidated_ts": bs["invalidated_ts"], "invalidate_reason": bs["invalidate_reason"],
            "is_active": bs["active"],
        })
    return pd.DataFrame(records), events


# ═══════════════════════════════════════════════════
#  编排纯函数（notebook / 测试直接调用）
# ═══════════════════════════════════════════════════

def compute_fib_retracement(klines: List[dict], cfg: RetracementConfig = None) -> dict:
    """多步长趋势腿提取 → 加权合并 → Fib 回撤。"""
    cfg = cfg or RetracementConfig()
    feature_df = base_df(klines)
    feature_df, w1 = tag_pivots(feature_df, cfg.pivot_windows)
    feature_df, w2 = tag_zigzag(feature_df, cfg.zigzag_thresholds)
    feature_df, w3 = tag_regression(feature_df, cfg.regression_windows)
    wmap = {**w1, **w2, **w3}
    feature_df = compute_confidence(feature_df, wmap, cfg.weights)
    clusters_high_df = cluster_prices(feature_df, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    clusters_low_df = cluster_prices(feature_df, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    n = len(feature_df)
    effective_end = max(0, n - cfg.skip_recent)
    effective_df = feature_df.iloc[:effective_end]
    log.debug(f'skip_recent={cfg.skip_recent} n={n} effective_end={effective_end}')
    all_groups, step_results = [], []
    for mult in (1, 2, 3):
        target_bars = cfg.recent_bars * mult
        actual_start = adaptive_window_start(effective_df, target_bars, min_conf=cfg.min_cluster_conf)
        recent_df = effective_df.iloc[actual_start:].reset_index(drop=True)
        legs = extract_trend_legs(recent_df, clusters_high_df, clusters_low_df, min_span_pct=cfg.min_leg_span_pct)
        ranked = score_and_rank(legs, top_n=cfg.top_n, total_bars=len(recent_df))
        up_legs = [lg for lg in ranked if lg.direction == "up"]
        down_legs = [lg for lg in ranked if lg.direction == "down"]
        merged = []
        if up_legs: merged.append(merge_legs_weighted(up_legs))
        if down_legs: merged.append(merge_legs_weighted(down_legs))
        groups = fit_fib_groups(merged, ratios=cfg.std_ratios)
        all_groups.extend(groups)
        step_results.append({"multiplier": mult, "target_bars": target_bars,
                             "actual_start": actual_start, "effective_end": effective_end,
                             "actual_bars": len(recent_df), "groups": groups,
                             "raw_legs": len(legs), "ranked_legs": len(ranked),
                             "up_merged": len(up_legs), "down_merged": len(down_legs)})
        log.debug(f'step×{mult}: target={target_bars} actual={len(recent_df)} legs={len(legs)} '
                  f'ranked={len(ranked)} up={len(up_legs)} down={len(down_legs)} groups={len(groups)}')
    return {"feature_df": feature_df, "effective_end": effective_end,
            "clusters_high_df": clusters_high_df, "clusters_low_df": clusters_low_df,
            "wmap": wmap, "groups": all_groups, "steps": step_results,
            "legs_found": sum(s["raw_legs"] for s in step_results),
            "legs_kept": sum(s["ranked_legs"] for s in step_results)}


# ═══════════════════════════════════════════════════
#  统一 Line 输出接口 (v4: 替代旧 parquet 分文件方案)
# ═══════════════════════════════════════════════════

def compute_lines_snapshot(feature_df: pd.DataFrame, cfg, compute_ts: int,
                           compute_bar_idx: int) -> List[Line]:
    """对一个时间点计算完整快照, 输出 List[Line]。
    1. 各 multiplier 独立产出 detected 线 (窗口不同看到的共识线不同)
    2. 合并所有 detected 线做统一 Fib 拟合, 输出 top-N 组最优 Fib
    """
    all_detected: List[Line] = []
    all_price_lines: List[PriceLine] = []
    mult_price_lines: Dict[int, List[PriceLine]] = {}
    for multiplier in (1, 2, 3):
        target_bars = cfg.recent_bars * multiplier
        actual_start = adaptive_window_start(feature_df, target_bars, min_conf=cfg.min_cluster_conf)
        recent_df = feature_df.iloc[actual_start:].reset_index(drop=True)
        if len(recent_df) < 10:
            continue
        price_lines = aggregate_price_lines(recent_df, cfg)
        if not price_lines:
            continue
        mult_price_lines[multiplier] = price_lines
        for pl in price_lines:
            all_detected.append(Line(
                compute_ts=compute_ts, compute_bar_idx=compute_bar_idx,
                multiplier=multiplier, type="detected", center=round(pl.center, 2),
                hit_count=pl.hit_count, total_conf=round(pl.total_conf, 4),
                time_span_ratio=round(pl.time_span_ratio, 4),
                has_high=pl.has_high, has_low=pl.has_low, is_bidirectional=pl.is_bidirectional,
                strength=round(pl.line_strength, 4), tolerance=round(pl.tolerance, 2),
            ))
    if not all_detected:
        return []
    # 合并去重: 不同 multiplier 可能发现相同价位的线, 按 center 合并取最强
    merged_map: Dict[float, PriceLine] = {}
    for mult, pls in mult_price_lines.items():
        for pl in pls:
            key = round(pl.center, 2)
            if key not in merged_map or pl.line_strength > merged_map[key].line_strength:
                merged_map[key] = pl
    merged_lines = sorted(merged_map.values(), key=lambda x: x.line_strength, reverse=True)
    # Fib 拟合: 用合并后的全部 detected 线, 选出 top-3 组
    top_n_fib = getattr(cfg, 'top_fib_groups', 3)
    fib_groups = fit_fib_top_n(merged_lines, cfg, top_n=top_n_fib)
    # 输出 fib lines
    lines = list(all_detected)
    for rank, fib_result in enumerate(fib_groups):
        H, L, quality = fib_result.leg_high, fib_result.leg_low, fib_result.fib_quality
        for lv in fib_result.levels:
            if abs(lv.ratio) < 0.001 or abs(lv.ratio - 1.0) < 0.001:
                ltype = "fib_extended"
            else:
                ltype = "fib_anchored"
            lines.append(Line(
                compute_ts=compute_ts, compute_bar_idx=compute_bar_idx,
                multiplier=rank + 1, type=ltype, center=round(lv.price, 2),
                fib_ratio=round(lv.ratio, 4), fib_quality=round(quality, 4),
                fib_leg_high=round(H, 2), fib_leg_low=round(L, 2),
                anchor_center=round(lv.price - lv.anchor_center, 2) if lv.anchor_center else None,
                strength=round(lv.anchor_strength, 4) if lv.anchor_strength else 0.0,
            ))
    det_count = len(all_detected)
    fib_count = len(lines) - det_count
    log.info(f'[compute_lines_snapshot] detected={det_count} fib_groups={len(fib_groups)} fib_lines={fib_count} total={len(lines)}')
    return lines


def fit_fib_top_n(price_lines: List[PriceLine], cfg, top_n: int = 3) -> List[FibResult]:
    """从合并的 price_lines 中拟合出 top-N 组最优 Fib 网格 (互不重叠)。"""
    if len(price_lines) < 2:
        return []
    std_ratios = cfg.std_ratios
    inner_ratios = [r for r in std_ratios if 0.001 < r < 0.999]
    top_k = min(cfg.top_lines_for_fit, len(price_lines))
    top_lines = sorted(price_lines, key=lambda x: x.line_strength, reverse=True)[:top_k]
    R_HIGH_ANCHOR, R_LOW_ANCHOR = 0.786, 0.236
    ANCHOR_SPAN = R_HIGH_ANCHOR - R_LOW_ANCHOR
    # 收集所有候选 (H, L, score, hi_anchor, lo_anchor)
    candidates = []
    for i in range(len(top_lines)):
        for j in range(i + 1, len(top_lines)):
            hi = top_lines[i] if top_lines[i].center > top_lines[j].center else top_lines[j]
            lo = top_lines[j] if top_lines[i].center > top_lines[j].center else top_lines[i]
            anchor_span = hi.center - lo.center
            if anchor_span <= 0:
                continue
            full_span = anchor_span / ANCHOR_SPAN
            L = lo.center - R_LOW_ANCHOR * full_span
            H = L + full_span
            if L <= 0 or full_span / L < cfg.min_leg_span_pct:
                continue
            score = hi.line_strength + lo.line_strength
            for pl in price_lines:
                if pl is hi or pl is lo:
                    continue
                ratio = (pl.center - L) / full_span
                if ratio < -0.05 or ratio > 1.05:
                    continue
                nearest_std = min(inner_ratios, key=lambda r: abs(r - ratio))
                error = abs(ratio - nearest_std)
                if error <= cfg.max_ratio_error:
                    score += pl.line_strength * (1.0 - error / cfg.max_ratio_error)
            candidates.append((score, H, L, hi, lo))
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0], reverse=True)
    # 选 top-N, 要求 H/L 不重叠 (重叠=两组的价格区间交叉超过50%)
    max_possible = sum(pl.line_strength for pl in price_lines)
    results = []
    used_ranges = []
    for score, H, L, hi_anchor, lo_anchor in candidates:
        if len(results) >= top_n:
            break
        # 重叠检查
        overlap = False
        for uH, uL in used_ranges:
            inter_lo, inter_hi = max(L, uL), min(H, uH)
            if inter_hi > inter_lo:
                overlap_pct = (inter_hi - inter_lo) / min(H - L, uH - uL)
                if overlap_pct > 0.5:
                    overlap = True; break
        if overlap:
            continue
        fib_quality = score / max_possible if max_possible > 0 else 0.0
        if fib_quality < cfg.min_fib_quality:
            continue
        # 生成 levels
        span = H - L
        levels = []
        for ratio in std_ratios:
            fib_price = L + span * ratio
            anchor = None
            if abs(ratio - R_LOW_ANCHOR) < 0.001:
                anchor = lo_anchor
            elif abs(ratio - R_HIGH_ANCHOR) < 0.001:
                anchor = hi_anchor
            else:
                best_dist = float('inf')
                for pl in price_lines:
                    d = abs(pl.center - fib_price)
                    if d < best_dist:
                        best_dist = d; anchor = pl
            levels.append(FibLevel(
                ratio=round(ratio, 4), price=round(fib_price, 4),
                is_anchored=anchor is not None,
                anchor_center=round(anchor.center, 4) if anchor else None,
                anchor_strength=round(anchor.line_strength, 4) if anchor else 0.0,
            ))
        results.append(FibResult(
            is_valid=True, fib_quality=round(fib_quality, 4),
            leg_high=round(H, 4), leg_low=round(L, 4),
            levels=levels, price_lines=price_lines,
        ))
        used_ranges.append((H, L))
    log.info(f'[fit_fib_top_n] candidates={len(candidates)} selected={len(results)} '
             f'qualities={[r.fib_quality for r in results]}')
    return results


# ═══════════════════════════════════════════════════
#  全局 Fib 拟合管线 (v5): 全配对 → 聚类 → 覆盖选取 → ratio线
# ═══════════════════════════════════════════════════

def fit_fib_global(centers: List[float], cfg) -> List[Tuple[float, float]]:
    """全历史 PriceLine 全配对拟合, 产出所有有效 Fib (leg_low, leg_high)。
    有效条件: 0.236/0.786 由两条PL锚定 + 0%/0.382/0.5/0.618 全部命中PL。
    """
    import numpy as np
    arr = np.array(sorted(set(round(c, 2) for c in centers)))
    n = len(arr)
    if n < 2: return []
    base_tol = getattr(cfg, 'fit_tolerance_pct', 0.003)
    tol_pct = base_tol * max(1.0, 80.0 / n)  # 稀疏时自适应放宽容差
    min_spread = getattr(cfg, 'fit_min_spread_pct', 0.02)
    max_spread = getattr(cfg, 'fit_max_spread_pct', 0.50)
    min_mid_hits = 2 if n < 100 else 3  # 稀疏时只需2/3中间ratio命中
    RATIOS_MID = [0.382, 0.5, 0.618]
    ANCHOR_LO, ANCHOR_HI = 0.236, 0.786
    ANCHOR_SPAN = ANCHOR_HI - ANCHOR_LO
    fibs = []
    for i in range(n):
        pl_a = arr[i]
        for j in range(i + 1, n):
            pl_b = arr[j]
            spread = (pl_b - pl_a) / pl_a
            if spread < min_spread or spread > max_spread: continue
            leg_range = (pl_b - pl_a) / ANCHOR_SPAN
            leg_low = pl_a - ANCHOR_LO * leg_range
            leg_high = leg_low + leg_range
            tol_0 = abs(leg_low) * tol_pct
            idx = np.searchsorted(arr, leg_low)
            hit_0 = any(0 <= k < n and abs(arr[k] - leg_low) <= tol_0 for k in [idx - 1, idx])
            if not hit_0: continue
            mid_hits = 0
            for ratio in RATIOS_MID:
                target = leg_low + leg_range * ratio
                tol = target * tol_pct
                idx2 = np.searchsorted(arr, target)
                for k in [idx2 - 1, idx2]:
                    if 0 <= k < n and abs(arr[k] - target) <= tol:
                        mid_hits += 1; break
            if mid_hits >= min_mid_hits:
                fibs.append((float(leg_low), float(leg_high)))
    log.info(f'[fit_fib_global] PL={n} tol={tol_pct:.4f} min_mid={min_mid_hits} valid_fibs={len(fibs)}')
    return fibs


def cluster_fibs(fibs: List[Tuple[float, float]], merge_tol: float = 0.02) -> List[dict]:
    """将相似 Fib 聚类合并 (leg_low/high 相对差 < merge_tol)。
    返回 [{leg_low, leg_high, consensus}] 按 consensus 降序。
    """
    import numpy as np
    if not fibs: return []
    fibs_sorted = sorted(fibs, key=lambda x: (x[0], x[1]))
    clusters = []
    used = [False] * len(fibs_sorted)
    for i in range(len(fibs_sorted)):
        if used[i]: continue
        cl_lo = [fibs_sorted[i][0]]
        cl_hi = [fibs_sorted[i][1]]
        for j in range(i + 1, len(fibs_sorted)):
            if used[j]: continue
            avg_lo, avg_hi = np.mean(cl_lo), np.mean(cl_hi)
            if (abs(fibs_sorted[j][0] - avg_lo) / avg_lo < merge_tol and
                abs(fibs_sorted[j][1] - avg_hi) / avg_hi < merge_tol):
                cl_lo.append(fibs_sorted[j][0])
                cl_hi.append(fibs_sorted[j][1])
                used[j] = True
        clusters.append({"leg_low": round(np.mean(cl_lo), 2), "leg_high": round(np.mean(cl_hi), 2),
                        "consensus": len(cl_lo)})
        used[i] = True
    clusters.sort(key=lambda x: -x["consensus"])
    log.info(f'[cluster_fibs] input={len(fibs)} → clusters={len(clusters)} '
             f'max_consensus={clusters[0]["consensus"] if clusters else 0}')
    return clusters


def select_fibs_by_coverage(clusters: List[dict]) -> List[dict]:
    """按共识度优先 + 覆盖增量贪心选取, 保证全覆盖。
    返回带 is_selected 标记的 clusters 列表。
    """
    import numpy as np
    if not clusters: return []
    all_lo = min(c["leg_low"] for c in clusters)
    all_hi = max(c["leg_high"] for c in clusters)
    price_lo, price_hi = int(all_lo), int(all_hi)
    size = price_hi - price_lo + 1
    # 可覆盖区域
    coverable = np.zeros(size, dtype=bool)
    for c in clusters:
        lo_i = max(0, int(c["leg_low"]) - price_lo)
        hi_i = min(size - 1, int(c["leg_high"]) - price_lo)
        coverable[lo_i:hi_i + 1] = True
    covered = np.zeros(size, dtype=bool)
    selected_indices = []
    # 按 consensus 降序已排好, 逐个检查覆盖增量
    for idx, c in enumerate(clusters):
        lo_i = max(0, int(c["leg_low"]) - price_lo)
        hi_i = min(size - 1, int(c["leg_high"]) - price_lo)
        new_cov = int(np.sum(~covered[lo_i:hi_i + 1] & coverable[lo_i:hi_i + 1]))
        if new_cov > 0:
            selected_indices.append(idx)
            covered[lo_i:hi_i + 1] = True
    for idx, c in enumerate(clusters):
        c["is_selected"] = idx in selected_indices
    n_sel = len(selected_indices)
    log.info(f'[select_fibs_by_coverage] clusters={len(clusters)} selected={n_sel}')
    return clusters


def score_clusters(clusters: List[dict], centers: List[float], min_score: float = 0.3) -> List[dict]:
    """对每个选中的 cluster 进行打分: 其 7 条 ratio 线与实际 PL 的对齐程度。
    score = 命中的 ratio 线数 / 7, 命中条件: 最近PL距离 < price*0.3%
    返回带 score 字段且按 score 降序的 clusters, is_selected 根据 min_score 重新标记。
    """
    import numpy as np
    arr = np.array(sorted(set(round(c, 2) for c in centers)))
    n = len(arr)
    RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    for c in clusters:
        if not c.get("is_selected"):
            c["score"] = 0.0; continue
        lo, hi = c["leg_low"], c["leg_high"]
        span = hi - lo
        hits = 0
        total_proximity = 0.0
        for r in RATIOS:
            target = lo + span * r
            tol = target * 0.003
            idx = np.searchsorted(arr, target)
            best_dist = float('inf')
            for k in [idx - 1, idx]:
                if 0 <= k < n:
                    best_dist = min(best_dist, abs(arr[k] - target))
            if best_dist <= tol:
                hits += 1
                total_proximity += 1.0 - best_dist / tol
        c["score"] = round(hits / 7.0 * 0.5 + total_proximity / 7.0 * 0.5, 4)
    # 重新标记 is_selected
    for c in clusters:
        c["is_selected"] = c.get("score", 0) >= min_score
    clusters.sort(key=lambda x: -x.get("score", 0))
    n_sel = sum(1 for c in clusters if c["is_selected"])
    log.info(f'[score_clusters] total={len(clusters)} scored, selected(>={min_score})={n_sel} '
             f'top_score={clusters[0]["score"] if clusters else 0}')
    return clusters


def expand_ratio_lines(clusters: List[dict]) -> List[dict]:
    """将选中的 Fib 簇展开为 ratio 线。
    返回 [{price, ratio, fib_low, fib_high, consensus, score}]
    """
    RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    lines = []
    for c in clusters:
        if not c.get("is_selected"): continue
        lo, hi, cons = c["leg_low"], c["leg_high"], c["consensus"]
        score = c.get("score", 0)
        span = hi - lo
        for r in RATIOS:
            lines.append({"price": round(lo + span * r, 2), "ratio": r,
                         "fib_low": lo, "fib_high": hi, "consensus": cons, "score": score})
    log.info(f'[expand_ratio_lines] selected_fibs={sum(1 for c in clusters if c.get("is_selected"))} '
             f'→ ratio_lines={len(lines)}')
    return lines
