"""Swing 特征计算：pivot + zigzag + regression + confidence + cluster。
纯函数独立可调，Command 编排 + CSV 落盘。
Jupyter: result = compute_swing_features(klines)
"""
import dataclasses, math
from typing import Any, ClassVar, Dict, List, Literal, Tuple
from pydantic import Field
from bollydog.globals import protocol
from bollydog.models.base import BaseCommand
from timing.analysis.config import FibConfig
from timing.analysis.types import PriceCluster
from timing.analysis.algo import dump_csv

# ── 纯函数：tag_pivots ──
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
        tags[col_h] = arr_h; tags[col_l] = arr_l
    return tags

# ── 纯函数：tag_zigzag ──
def tag_zigzag(klines: List[dict], thresholds: List[float]) -> Dict[str, List[float]]:
    n = len(klines)
    tags: Dict[str, List[float]] = {}
    for thr in thresholds:
        pct = int(thr * 100)
        col_h, col_l = f"zigzag_high_{pct}", f"zigzag_low_{pct}"
        arr_h, arr_l = [math.nan] * n, [math.nan] * n
        if n == 0:
            tags[col_h] = arr_h; tags[col_l] = arr_l; continue
        state, last_hi, last_lo = "init", klines[0]["high"], klines[0]["low"]
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

# ── 纯函数：tag_regression ──
def _linreg_residuals(values: List[float]) -> List[float]:
    n = len(values)
    if n < 3: return [0.0] * n
    sx, sx2 = n * (n - 1) / 2, n * (n - 1) * (2 * n - 1) / 6
    sy = sum(values); sxy = sum(i * v for i, v in enumerate(values))
    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-12: return [0.0] * n
    slope = (n * sxy - sx * sy) / denom; intercept = (sy - slope * sx) / n
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
            if residuals[-1] > 2 * std: arr_h[i] = klines[i]["high"]
            elif residuals[-1] < -2 * std: arr_l[i] = klines[i]["low"]
        tags[col_h] = arr_h; tags[col_l] = arr_l
    return tags

# ── 纯函数：compute_confidence ──
def compute_confidence(tags: Dict[str, List[float]], weights: Dict[str, float]) -> Tuple[List[float], List[float]]:
    if not tags: return [], []
    n = len(next(iter(tags.values())))
    conf_high, conf_low = [0.0] * n, [0.0] * n
    max_w = sum(weights.values()) or 1.0
    for col, arr in tags.items():
        w_key = col.replace("_high", "").replace("_low", "")
        w = weights.get(w_key, 0.5)
        is_high = "_high" in col
        for i in range(n):
            if not math.isnan(arr[i]):
                if is_high: conf_high[i] += w
                else: conf_low[i] += w
    for i in range(n):
        conf_high[i] = min(conf_high[i] / max_w, 1.0)
        conf_low[i] = min(conf_low[i] / max_w, 1.0)
    return conf_high, conf_low

# ── 纯函数：cluster_prices ──
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
        center = sum(pp * cc for pp, cc, _ in clusters[-1]) / sum(cc for _, cc, _ in clusters[-1])
        if abs(p - center) <= tol: clusters[-1].append((p, c, idx))
        else: clusters.append([(p, c, idx)])
    result = []
    for cl in clusters:
        total_conf = sum(c for _, c, _ in cl)
        center = sum(p * c for p, c, _ in cl) / total_conf
        last_idx = max(idx for _, _, idx in cl)
        result.append(PriceCluster(center=round(center, 6), hit_count=len(cl), total_conf=round(total_conf, 4), last_index=last_idx, kind=kind))
    return result

# ── Command ──
class ComputeSwingFeatures(BaseCommand):
    """计算拐点特征 step 1-5 + CSV 落盘。纯函数链路内联。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeSwingFeatures"
    klines: List[dict] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    symbol: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        cfg = FibConfig(**(self.config or {}))
        tags: Dict[str, List[float]] = {}
        tags.update(tag_pivots(self.klines, cfg.pivot_windows))
        tags.update(tag_zigzag(self.klines, cfg.zigzag_thresholds))
        tags.update(tag_regression(self.klines, cfg.regression_windows))
        conf_high, conf_low = compute_confidence(tags, cfg.weights)
        clusters_high = cluster_prices(self.klines, conf_high, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        clusters_low = cluster_prices(self.klines, conf_low, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        result = {
            "tags": tags, "conf_high": conf_high, "conf_low": conf_low,
            "clusters_high": [dataclasses.asdict(c) for c in clusters_high],
            "clusters_low": [dataclasses.asdict(c) for c in clusters_low],
        }
        if self.symbol and protocol:
            try: self._save_csv(result, cfg)
            except Exception: pass
        return result

    def _save_csv(self, result: dict, cfg: FibConfig):
        pw = "_".join(f"{l}x{r}" for l, r in cfg.pivot_windows)
        zz = "_".join(str(int(t * 100)) for t in cfg.zigzag_thresholds)
        rw = "_".join(str(w) for w in cfg.regression_windows)
        tag_cols = sorted(result["tags"].keys())
        header = ["ts", "open", "high", "low", "close", "volume"] + tag_cols + ["conf_high", "conf_low"]
        rows = []
        for i, k in enumerate(self.klines):
            row = [k["ts"], k["open"], k["high"], k["low"], k["close"], k.get("volume", 0)]
            for col in tag_cols: row.append(result["tags"][col][i])
            row.append(result["conf_high"][i]); row.append(result["conf_low"][i])
            rows.append(tuple(row))
        dump_csv(f"tmp/{self.symbol}_swing_tags_p{pw}_z{zz}_r{rw}.csv", header, rows)
        all_cl = result["clusters_high"] + result["clusters_low"]
        if all_cl:
            cl_rows = [(c["kind"], c["center"], c["hit_count"], c["total_conf"], c["last_index"]) for c in all_cl]
            dump_csv(f"tmp/{self.symbol}_swing_clusters.csv", ["kind", "center", "hit_count", "total_conf", "last_index"], cl_rows)
