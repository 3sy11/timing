"""Retracement 一体化 pipeline：swing 拐点识别 → 聚类 → 趋势腿提取 → Fib 回撤。

纯函数全部在此文件，Command 只做编排 + 存缓存。
"""
import logging, math
from typing import Any, ClassVar, Dict, List, Literal, Tuple
import pandas as pd
from pydantic import Field
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from .config import RetracementConfig, DEFAULT_RATIOS
from .models import TrendLeg, FibGroup, FibLevelTouched, FibInvalidated

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
    """从 conf_high/conf_low 筛选出的显著拐点两两配对，生成候选趋势腿（up + down）。"""
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
            else:
                continue
            span_pct = (high_p - low_p) / low_p if low_p > 0 else 0
            if span_pct < min_span_pct: continue
            bonus_a = _cluster_bonus(price_a, cluster_centers_l if kind_a == "low" else cluster_centers_h)
            bonus_b = _cluster_bonus(price_b, cluster_centers_h if kind_b == "high" else cluster_centers_l)
            conf_score = (conf_a + bonus_a) + (conf_b + bonus_b)
            legs.append(TrendLeg(start_idx=idx_a, end_idx=idx_b, start_ts=ts_a, end_ts=ts_b,
                                 low=low_p, high=high_p, direction=direction,
                                 span_pct=span_pct, conf_score=conf_score))
    return legs


def score_and_rank(legs: List[TrendLeg], top_n: int = 6) -> List[TrendLeg]:
    """按 span_pct × conf_score 打分，去冗余腿，返回 top-N。"""
    if not legs: return []
    for lg in legs: lg.conf_score = lg.span_pct * lg.conf_score
    legs.sort(key=lambda x: x.conf_score, reverse=True)
    kept: List[TrendLeg] = []
    for lg in legs:
        redundant = False
        for k in kept:
            if k.direction == lg.direction and k.start_idx <= lg.start_idx and k.end_idx >= lg.end_idx:
                redundant = True; break
        if not redundant: kept.append(lg)
        if len(kept) >= top_n: break
    return kept


def compute_retracement_levels(leg: TrendLeg, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    """up 腿：level = high - span*ratio（从高回撤）; down 腿：level = low + span*ratio（从低反弹）。"""
    span = leg.high - leg.low
    if leg.direction == "up": return [(r, leg.high - span * r) for r in ratios]
    return [(r, leg.low + span * r) for r in ratios]


def fit_fib_groups(legs: List[TrendLeg], ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[FibGroup]:
    groups: List[FibGroup] = []
    for lg in legs:
        levels = compute_retracement_levels(lg, ratios)
        groups.append(FibGroup(leg=lg, levels=levels, score=lg.conf_score, direction=lg.direction))
    return groups


# ═══════════════════════════════════════════════════
#  编排函数（notebook 可直接调用，不依赖 app）
# ═══════════════════════════════════════════════════

def compute_retracement(klines: List[dict], cfg: RetracementConfig = None) -> dict:
    """纯函数版本：klines → feature_df + clusters + fib_groups，不涉及 app/hub。"""
    cfg = cfg or RetracementConfig()
    feature_df = base_df(klines)
    feature_df, w1 = tag_pivots(feature_df, cfg.pivot_windows)
    feature_df, w2 = tag_zigzag(feature_df, cfg.zigzag_thresholds)
    feature_df, w3 = tag_regression(feature_df, cfg.regression_windows)
    wmap = {**w1, **w2, **w3}
    feature_df = compute_confidence(feature_df, wmap, cfg.weights)
    clusters_high_df = cluster_prices(feature_df, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    clusters_low_df = cluster_prices(feature_df, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    legs = extract_trend_legs(feature_df, clusters_high_df, clusters_low_df, min_span_pct=cfg.min_leg_span_pct)
    ranked = score_and_rank(legs, top_n=cfg.top_n)
    groups = fit_fib_groups(ranked, ratios=cfg.std_ratios)
    return {"feature_df": feature_df, "clusters_high_df": clusters_high_df, "clusters_low_df": clusters_low_df,
            "wmap": wmap, "groups": groups, "legs_found": len(legs), "legs_kept": len(ranked)}


# ═══════════════════════════════════════════════════
#  Command（生产用，写入 app 缓存 + 广播事件）
# ═══════════════════════════════════════════════════

class ComputeRetracement(BaseCommand):
    """完整 pipeline：klines → swing → fib groups → 存入 retracement service 缓存。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeRetracement"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    klines: List[dict] = Field(default_factory=list)

    async def __call__(self, *args, **kwargs) -> Any:
        klines = self.klines
        if not klines:
            from timing.engine.cache import GetKlines
            gk = GetKlines(symbol=self.symbol, interval=self.interval, qos=0)
            await hub.execute(gk); klines = await gk.state
        if not klines: return {"error": "no klines"}
        cfg = app.retracement.config if hasattr(app, "retracement") else RetracementConfig()
        result = compute_retracement(klines, cfg)
        groups = result["groups"]
        if self.symbol and hasattr(app, "retracement"):
            app.retracement.set_cache(self.symbol, self.interval, result)
            app.save(self.symbol, self.interval)
        log.info(f'[Retracement] {self.symbol}/{self.interval} legs_found={result["legs_found"]} groups={len(groups)}')
        return {"groups": [{"direction": g.direction, "score": round(g.score, 4),
                            "leg": {"start_ts": g.leg.start_ts, "end_ts": g.leg.end_ts,
                                    "low": g.leg.low, "high": g.leg.high, "span_pct": round(g.leg.span_pct, 4)},
                            "levels": [{"ratio": r, "price": round(p, 6)} for r, p in g.levels]}
                           for g in groups],
                "legs_found": result["legs_found"], "legs_kept": result["legs_kept"]}


# ═══════════════════════════════════════════════════
#  第四阶段：触碰检测 + 突破失效
# ═══════════════════════════════════════════════════

def check_touch(price: float, levels: List[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    return [(r, p) for r, p in levels if abs(price - p) <= tolerance]


def check_breakout(close: float, groups: List[FibGroup], tolerance: float = 0.0) -> List[Tuple[int, str, str]]:
    """close 超出 [leg.low, leg.high] → 该组失效。"""
    broken = []
    for i, g in enumerate(groups):
        if close > g.leg.high + tolerance: broken.append((i, g.direction, "above_high"))
        elif close < g.leg.low - tolerance: broken.append((i, g.direction, "below_low"))
    return broken


class CheckTouch(BaseCommand):
    """每根 bar 调用：close 与缓存 fib 线碰撞检测（带冷却），触碰时广播 FibLevelTouched。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.CheckTouch"
    symbol: str = ""; interval: str = ""
    bar: dict = {}

    async def __call__(self, *args, **kwargs) -> Any:
        price = float(self.bar.get("close", 0))
        if not price: return []
        all_levels = app.retracement.get_all_levels(self.symbol, self.interval)
        if not all_levels: return []
        levels_flat = [(r, p) for r, p, _d, _s in all_levels]
        touched = app.retracement.check_touch_with_cooldown(self.symbol, self.interval, price, levels_flat)
        for r, p in touched:
            await hub.emit(FibLevelTouched(symbol=self.symbol, ratio=r, level_price=p, touch_price=price))
        if touched: log.info(f'[Retracement] touch {self.symbol} price={price} hit={len(touched)}')
        return touched


class CheckBreakout(BaseCommand):
    """每根 bar 调用：close 是否突破趋势腿边界 → 废弃该组 → 广播 FibInvalidated → need_recompute。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.CheckBreakout"
    symbol: str = ""; interval: str = ""
    bar: dict = {}

    async def __call__(self, *args, **kwargs) -> Any:
        close = float(self.bar.get("close", 0))
        if not close: return {"broken": [], "need_recompute": False}
        cache = app.retracement.get_cache(self.symbol, self.interval)
        groups = cache.get("groups", []) if cache else []
        if not groups: return {"broken": [], "need_recompute": False}
        tol = app.retracement.config.breakout_tolerance
        broken = check_breakout(close, groups, tolerance=tol)
        if not broken: return {"broken": [], "need_recompute": False}
        cache["groups"] = [g for i, g in enumerate(groups) if i not in {idx for idx, _, _ in broken}]
        for idx, direction, side in broken:
            await hub.emit(FibInvalidated(symbol=self.symbol, interval=self.interval,
                                          group_idx=idx, direction=direction, break_side=side, close=close))
        log.info(f'[Retracement] breakout {self.symbol} close={close} broken={len(broken)} → need_recompute')
        return {"broken": broken, "need_recompute": True}
