"""Retracement 纯函数：swing 拐点识别 → 聚类 → 趋势腿提取 → Fib 回撤 → 碰撞/突破检测。

所有函数不依赖 app/hub，可在 notebook / 测试中直接调用。
"""
import logging, math
from typing import Dict, List, Literal, Tuple
import pandas as pd
from . import config
from .models import TrendLeg, FibGroup

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
        # 原始分：span × conf
        base = lg.span_pct * lg.conf_score
        
        # 近期衰减：腿的终点离当前越近权重越高
        # recency = 1.0（终点就是最新bar）→ 0.0（终点在很久以前）
        recency = lg.end_idx / max_idx if max_idx > 0 else 1.0
        
        # 腿的长度惩罚：太长的腿（跨度超过总数据60%）降权
        # 鼓励选近期的中等长度腿，而不是横跨全量数据的腿
        length_ratio = (lg.end_idx - lg.start_idx) / max_idx if max_idx > 0 else 1.0
        length_penalty = 1.0 if length_ratio < 0.6 else (1.0 - (length_ratio - 0.6) / 0.4 * 0.7)
        
        lg.conf_score = base * recency * length_penalty
    
    legs.sort(key=lambda x: x.conf_score, reverse=True)
    
    # 强制多样性选腿：up 和 down 各取一半
    kept_up, kept_down = [], []
    quota_each = top_n // 2  # 比如 top_n=6 → 各取3条
    
    for lg in legs:
        if lg.direction == "up" and len(kept_up) < quota_each:
            # 去重：不选被更高分的up腿完全包含的腿
            if not any(k.start_idx <= lg.start_idx and k.end_idx >= lg.end_idx 
                      for k in kept_up):
                kept_up.append(lg)
        elif lg.direction == "down" and len(kept_down) < quota_each:
            if not any(k.start_idx <= lg.start_idx and k.end_idx >= lg.end_idx 
                      for k in kept_down):
                kept_down.append(lg)
        
        if len(kept_up) >= quota_each and len(kept_down) >= quota_each:
            break
    
    # up 和 down 交替排列，方便图上区分
    result = []
    for u, d in zip(kept_up, kept_down):
        result.extend([u, d])
    # 补充剩余（如果某方向不够）
    result.extend(kept_up[len(result)//2:])
    result.extend(kept_down[len(result)//2:])
    
    return result[:top_n]


def adaptive_window_start(feature_df: pd.DataFrame, base_bars: int, min_conf: float = 0.1) -> int:
    """从末尾回溯，若窗口边界处连续同方向拐点，延伸到第二次方向转变。"""
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
    """同方向多条趋势腿按 conf_score 加权平均为一条。"""
    if not legs: return None
    if len(legs) == 1: return legs[0]
    total_w = sum(lg.conf_score for lg in legs) or 1.0
    def wavg(attr): return sum(getattr(lg, attr) * lg.conf_score for lg in legs) / total_w
    low, high = wavg("low"), wavg("high")
    return TrendLeg(start_idx=int(round(wavg("start_idx"))), end_idx=int(round(wavg("end_idx"))),
                    start_ts=int(round(wavg("start_ts"))), end_ts=int(round(wavg("end_ts"))),
                    low=low, high=high, direction=legs[0].direction,
                    span_pct=(high - low) / low if low > 0 else 0, conf_score=total_w)


def compute_retracement_levels(leg: TrendLeg, ratios: Tuple[float, ...] = None) -> List[Tuple[float, float]]:
    ratios = ratios or config.ALGO_STD_RATIOS
    span = leg.high - leg.low
    if leg.direction == "up": return [(r, leg.high - span * r) for r in ratios]
    return [(r, leg.low + span * r) for r in ratios]


def fit_fib_groups(legs: List[TrendLeg], ratios: Tuple[float, ...] = None) -> List[FibGroup]:
    ratios = ratios or config.ALGO_STD_RATIOS
    return [FibGroup(leg=lg, levels=compute_retracement_levels(lg, ratios), score=lg.conf_score, direction=lg.direction) for lg in legs]


# ═══════════════════════════════════════════════════
#  编排纯函数（notebook / 测试直接调用）
# ═══════════════════════════════════════════════════

def compute_retracement(klines: List[dict]) -> dict:
    """多步长趋势腿提取 → 加权合并 → Fib 回撤。
    参数全部从 config 模块读取。
    每步(×1,×2,×3)独立：自适应窗口 → 选腿(top_n/2 up + top_n/2 down)
    → 同方向加权合并为一条 → 拟合 Fib。每步最多 2 组(1 up + 1 down)。
    """
    feature_df = base_df(klines)
    feature_df, w1 = tag_pivots(feature_df, config.ALGO_PIVOT_WINDOWS)
    feature_df, w2 = tag_zigzag(feature_df, config.ALGO_ZIGZAG_THRESHOLDS)
    feature_df, w3 = tag_regression(feature_df, config.ALGO_REGRESSION_WINDOWS)
    wmap = {**w1, **w2, **w3}
    feature_df = compute_confidence(feature_df, wmap, config.ALGO_WEIGHTS)
    clusters_high_df = cluster_prices(feature_df, "high", config.ALGO_CLUSTER_TOLERANCE_PCT, config.ALGO_MIN_CLUSTER_CONF)
    clusters_low_df = cluster_prices(feature_df, "low", config.ALGO_CLUSTER_TOLERANCE_PCT, config.ALGO_MIN_CLUSTER_CONF)
    n = len(feature_df)
    effective_end = max(0, n - config.ALGO_SKIP_RECENT)
    effective_df = feature_df.iloc[:effective_end]
    log.debug(f'skip_recent={config.ALGO_SKIP_RECENT} n={n} effective_end={effective_end}')
    all_groups, step_results = [], []
    for mult in (1, 2, 3):
        target_bars = config.ALGO_RECENT_BARS * mult
        actual_start = adaptive_window_start(effective_df, target_bars, min_conf=config.ALGO_MIN_CLUSTER_CONF)
        recent_df = effective_df.iloc[actual_start:].reset_index(drop=True)
        legs = extract_trend_legs(recent_df, clusters_high_df, clusters_low_df, min_span_pct=config.ALGO_MIN_LEG_SPAN_PCT)
        ranked = score_and_rank(legs, top_n=config.ALGO_TOP_N, total_bars=len(recent_df))
        up_legs = [lg for lg in ranked if lg.direction == "up"]
        down_legs = [lg for lg in ranked if lg.direction == "down"]
        merged = []
        if up_legs: merged.append(merge_legs_weighted(up_legs))
        if down_legs: merged.append(merge_legs_weighted(down_legs))
        groups = fit_fib_groups(merged, ratios=config.ALGO_STD_RATIOS)
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
