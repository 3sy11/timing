"""price_touch：当天碰到存活 Fib 线，挂上拟合前/窗对照与近处 Fib/中心的弹、磨。"""
from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

from computation.algo.fib_retracement.line_events import fib_group_id
from .config import PriceTouchConfig

log = logging.getLogger(__name__)

CANDIDATE_COLS = [
    # ── A 组: 标识与上下文 (原有 12 列) ──
    "ts", "close", "approach", "ratio", "level_price",
    "fib_group_id", "multiplier", "direction",
    "effective_ts", "invalidated_ts", "compute_id", "feature_id",
    # ── B 组: 线级历史特征 (原有 8 列) ──
    "pre_test_n", "pre_hold_n", "pre_hold_rate", "pre_weak_share",
    "fit_test_n", "fit_hold_n", "fit_hold_rate", "fit_weak_share",
    # ── C 组: 邻居共振特征 (原有 10 列) ──
    "nearby_fib_n", "nearby_fib_hold_n", "nearby_fib_hold_rate",
    "nearby_fib_weak_n", "nearby_fib_weak_share",
    "nearby_cluster_n", "nearby_cluster_hold_n", "nearby_cluster_hold_rate",
    "nearby_cluster_weak_n", "nearby_cluster_weak_share",
    # ── D 组: 周期与连续接近度因子 (新增 4 列) ──
    "period", "proximity", "bar_position", "distance_pct",
    # ── E 组: 样本信心与线质量评分 (新增 10 列) ──
    "sample_conf", "fit_sample_conf",
    "score_hold", "score_weak", "score_fit",
    "score_cluster", "score_cluster_conf", "score_nearby",
    "line_quality", "candidate_score",
    # ── F 组: 组级元信息与标签 (新增 5 列) ──
    "group_score", "group_rank", "is_selected_group",
    "is_extrapolated", "fib_score",
    # ── G 组: 触碰波段与极值去重标签 (方案 1 新增 4 列) ──
    "episode_id", "touch_seq", "is_first_touch", "is_peak_touch",
]


def _is_num(v) -> bool:
    if v is None:
        return False
    try:
        return not (isinstance(v, float) and math.isnan(v))
    except TypeError:
        return True


def _agg(outcomes: list) -> dict:
    b = k = w = 0
    for o in outcomes:
        if o == "bounce":
            b += 1
        elif o == "break":
            k += 1
        elif o == "weak":
            w += 1
    test_n = b + k + w
    hold_n = b + k
    return {
        "test_n": test_n,
        "hold_n": hold_n,
        "hold_rate": round(b / hold_n, 4) if hold_n else None,
        "weak_share": round(w / test_n, 4) if test_n else None,
    }


def _mean(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _build_stats(events: pd.DataFrame) -> dict:
    if events.empty:
        return {}
    out = {}
    for rec in events.to_dict("records"):
        if rec.get("outcome_atr") is None or (isinstance(rec.get("outcome_atr"), float) and math.isnan(rec["outcome_atr"])):
            continue
        key = (
            str(rec["fib_group_id"]),
            round(float(rec["ratio"]), 4),
            str(rec["target_kind"]),
            str(rec["period"]),
        )
        out.setdefault(key, []).append(rec["outcome_atr"])
    return {k: _agg(v) for k, v in out.items()}


def _stats_for(stats: dict, gid: str, ratio: float, kind: str, period: str) -> dict:
    return stats.get((gid, round(float(ratio), 4), kind, period), _agg([]))


def run_detection(
    klines_df: pd.DataFrame,
    result_df: pd.DataFrame,
    events_df: pd.DataFrame,
    cfg: PriceTouchConfig = None,
    compute_id: str = "",
    feature_id: str = "",
    ranking_df: pd.DataFrame = None,
) -> dict:
    cfg = cfg or PriceTouchConfig()
    empty = {"candidates": [], "summary": {"total_candidates": 0}}
    if klines_df.empty or result_df.empty:
        return empty

    # 组级评分索引与过滤
    ranking_map = {}
    selected_gids = set()
    if ranking_df is not None and not ranking_df.empty:
        for r in ranking_df.to_dict("records"):
            gid = str(r["fib_group_id"])
            ranking_map[gid] = r
            if r.get("is_selected", False):
                selected_gids.add(gid)

    rows = result_df.to_dict("records")
    for rec in rows:
        rec["fib_group_id"] = fib_group_id(
            rec["effective_ts"], rec["multiplier"], rec["direction"],
            rec["leg_low"], rec["leg_high"],
        )
        rec["_price"] = float(rec["price"])
        rec["_ratio"] = float(rec["ratio"])
        rec["_eff"] = int(rec["effective_ts"])
        rec["_inv"] = rec.get("invalidated_ts")
        rec["_mult"] = int(rec["multiplier"])
        rec["_dir"] = str(rec["direction"])
        pl = rec.get("nearest_cluster_center")
        rec["_center"] = float(pl) if _is_num(pl) else None
        rec["_leg_start"] = int(rec["leg_start_ts"]) if "leg_start_ts" in rec and _is_num(rec["leg_start_ts"]) else rec["_eff"]
        rec["_is_extrapolated"] = bool(rec.get("is_extrapolated", False))
        rec["_fib_score"] = float(rec["fib_score"]) if "fib_score" in rec and _is_num(rec["fib_score"]) else None
        rec["_is_cluster_aligned"] = bool(rec.get("is_cluster_aligned", False))
        rec["_cluster_conf"] = float(rec["nearest_cluster_conf"]) if "nearest_cluster_conf" in rec and _is_num(rec["nearest_cluster_conf"]) else None

    # 组过滤与存活组筛选
    if getattr(cfg, "use_group_filter", False) and selected_gids:
        rows = [r for r in rows if r["fib_group_id"] in selected_gids]
        if not rows:
            return empty
    elif getattr(cfg, "include_pre_effective", False):
        # 对照组（未启用组过滤但需全周期回溯）：筛选当前仍存活的有效组，避免历史已废弃组的未来泄露
        alive_rows = [r for r in rows if not _is_num(r.get("invalidated_ts"))]
        if alive_rows:
            rows = alive_rows

    kdf = klines_df.sort_values("ts").reset_index(drop=True)
    ts = kdf["ts"].to_numpy(dtype=np.int64)
    highs = kdf["high"].to_numpy(dtype=np.float64)
    lows = kdf["low"].to_numpy(dtype=np.float64)
    closes = kdf["close"].to_numpy(dtype=np.float64)
    n = len(kdf)
    start = max(0, n - int(cfg.scan_bars)) if int(cfg.scan_bars) > 0 else 0
    pk = float(cfg.proximity_k)
    nk = float(cfg.neighbor_k)
    include_pre = bool(getattr(cfg, "include_pre_effective", False))

    stats = _build_stats(events_df)
    prices = np.array([r["_price"] for r in rows], dtype=np.float64)
    effs = np.array([r["_eff"] for r in rows], dtype=np.int64)
    invs = np.array([
        -1 if (r["_inv"] is None or (isinstance(r["_inv"], float) and math.isnan(r["_inv"]))) else int(r["_inv"])
        for r in rows
    ], dtype=np.int64)

    out = []
    for i in range(start, n):
        t = int(ts[i])
        close = float(closes[i])
        if include_pre:
            alive = (invs < 0) | (t < invs)
        else:
            alive = (effs <= t) & ((invs < 0) | (t < invs))
        lo = float(lows[i]) / (1.0 + pk)
        hi = float(highs[i]) / (1.0 - pk) if pk < 1 else float(highs[i])
        touched = alive & (prices >= lo) & (prices <= hi)
        hit_idx = np.flatnonzero(touched)
        if hit_idx.size == 0:
            continue
        alive_idx = np.flatnonzero(alive)
        for j in hit_idx:
            rec = rows[j]
            pre = _stats_for(stats, rec["fib_group_id"], rec["_ratio"], "fib", "prefit")
            if (not cfg.emit_if_no_prefit) and pre["hold_n"] == 0:
                continue
            fit = _stats_for(stats, rec["fib_group_id"], rec["_ratio"], "fib", "fitwin")
            p0 = rec["_price"]
            band = p0 * nk
            fib_hold, fib_weak = [], []
            cluster_by_px: dict[float, list] = {}
            for a in alive_idx:
                other = rows[a]
                if a != j and abs(other["_price"] - p0) <= band:
                    st = _stats_for(stats, other["fib_group_id"], other["_ratio"], "fib", "prefit")
                    if st["hold_rate"] is not None:
                        fib_hold.append(st["hold_rate"])
                    if st["weak_share"] is not None:
                        fib_weak.append(st["weak_share"])
                c = other["_center"]
                if c is not None and abs(c - p0) <= band:
                    cluster_by_px.setdefault(round(c, 2), []).append(other)
            cl_hold, cl_weak = [], []
            for members in cluster_by_px.values():
                hs, ws = [], []
                for m in members:
                    st = _stats_for(stats, m["fib_group_id"], m["_ratio"], "cluster", "prefit")
                    if st["hold_rate"] is not None:
                        hs.append(st["hold_rate"])
                    if st["weak_share"] is not None:
                        ws.append(st["weak_share"])
                if hs:
                    cl_hold.append(sum(hs) / len(hs))
                if ws:
                    cl_weak.append(sum(ws) / len(ws))
            if i > 0:
                approach = "from_above" if float(closes[i - 1]) > p0 else "from_below"
            else:
                approach = "from_above" if close >= p0 else "from_below"
            inv = rec["_inv"]

            # ── 周期判定 (D 组) ──
            leg_start = rec["_leg_start"]
            eff_ts = rec["_eff"]
            if t < leg_start:
                period = "prefit"
            elif t <= eff_ts:
                period = "fitwin"
            else:
                period = "predict"

            # ── 连续接近度 (D 组) ──
            ak = float(getattr(cfg, "attention_zone_k", 0.005))
            dist = abs(close - p0)
            proximity = round(max(0.0, 1.0 - dist / (p0 * ak)), 4) if (p0 * ak) > 0 else 0.0
            distance_pct = round(dist / p0, 6) if p0 > 0 else 0.0
            if close > p0 * 1.001:
                bar_position = "above"
            elif close < p0 * 0.999:
                bar_position = "below"
            else:
                bar_position = "crossing"

            # ── 样本信心与评分 (E 组) ──
            sc_cap = max(int(getattr(cfg, "sample_cap", 5)), 1)
            pre_hold_n = pre["hold_n"]
            sample_conf = round(min(pre_hold_n, sc_cap) / sc_cap, 4)
            fit_hold_n = fit["hold_n"]
            fit_sample_conf = round(min(fit_hold_n, sc_cap) / sc_cap, 4)

            w_hold = float(getattr(cfg, "w_hold", 3.0))
            w_weak = float(getattr(cfg, "w_weak", 1.0))
            w_fit = float(getattr(cfg, "w_fit", 1.0))
            w_cluster = float(getattr(cfg, "w_cluster", 1.0))
            w_cluster_conf = float(getattr(cfg, "w_cluster_conf", 0.5))
            w_nearby = float(getattr(cfg, "w_nearby", 1.0))

            score_hold = round(w_hold * (pre["hold_rate"] or 0.0) * sample_conf, 4)
            score_weak = round(-(w_weak * (pre["weak_share"] or 0.0)), 4)
            score_fit = round(w_fit * (fit["hold_rate"] or 0.0) * fit_sample_conf, 4)
            score_cluster = round(w_cluster * (1.0 if rec["_is_cluster_aligned"] else 0.0), 4)

            conf_val = rec["_cluster_conf"]
            conf_norm = min(max(conf_val, 0.0), 1.0) if conf_val is not None else 0.0
            score_cluster_conf = round(w_cluster_conf * conf_norm, 4)

            fib_cap = max(int(getattr(cfg, "nearby_fib_cap", 3)), 1)
            cls_cap = max(int(getattr(cfg, "nearby_cls_cap", 2)), 1)
            fib_n_part = min(len(fib_hold), fib_cap) / fib_cap
            nb_rate = _mean(fib_hold) or 0.0
            cls_n_part = min(len(cluster_by_px), cls_cap) / cls_cap
            score_nearby = round(w_nearby * (fib_n_part * 0.4 + nb_rate * 0.4 + cls_n_part * 0.2), 4)

            line_quality = round(score_hold + score_weak + score_fit + score_cluster + score_cluster_conf + score_nearby, 4)
            candidate_score = round(line_quality * proximity, 4)

            # ── 组级标签 (F 组) ──
            ginfo = ranking_map.get(rec["fib_group_id"])
            group_score = round(float(ginfo["group_score"]), 4) if ginfo and "group_score" in ginfo and ginfo["group_score"] is not None else None
            group_rank = int(ginfo["rank"]) if ginfo and "rank" in ginfo and ginfo["rank"] is not None else None
            is_selected_group = bool(ginfo.get("is_selected", False)) if ginfo else False

            out.append({
                # ── A 组 ──
                "ts": t, "close": close, "approach": approach,
                "ratio": rec["_ratio"], "level_price": p0,
                "fib_group_id": rec["fib_group_id"],
                "multiplier": rec["_mult"], "direction": rec["_dir"],
                "effective_ts": rec["_eff"],
                "invalidated_ts": None if not _is_num(inv) else int(inv),
                "compute_id": compute_id, "feature_id": feature_id,
                # ── B 组 ──
                "pre_test_n": pre["test_n"], "pre_hold_n": pre["hold_n"],
                "pre_hold_rate": pre["hold_rate"], "pre_weak_share": pre["weak_share"],
                "fit_test_n": fit["test_n"], "fit_hold_n": fit["hold_n"],
                "fit_hold_rate": fit["hold_rate"], "fit_weak_share": fit["weak_share"],
                # ── C 组 ──
                "nearby_fib_n": int(sum(
                    1 for a in alive_idx if a != j and abs(rows[a]["_price"] - p0) <= band
                )),
                "nearby_fib_hold_n": len(fib_hold),
                "nearby_fib_hold_rate": _mean(fib_hold),
                "nearby_fib_weak_n": len(fib_weak),
                "nearby_fib_weak_share": _mean(fib_weak),
                "nearby_cluster_n": len(cluster_by_px),
                "nearby_cluster_hold_n": len(cl_hold),
                "nearby_cluster_hold_rate": _mean(cl_hold),
                "nearby_cluster_weak_n": len(cl_weak),
                "nearby_cluster_weak_share": _mean(cl_weak),
                # ── D 组 ──
                "period": period,
                "proximity": proximity,
                "bar_position": bar_position,
                "distance_pct": distance_pct,
                # ── E 组 ──
                "sample_conf": sample_conf,
                "fit_sample_conf": fit_sample_conf,
                "score_hold": score_hold,
                "score_weak": score_weak,
                "score_fit": score_fit,
                "score_cluster": score_cluster,
                "score_cluster_conf": score_cluster_conf,
                "score_nearby": score_nearby,
                "line_quality": line_quality,
                "candidate_score": candidate_score,
                # ── F 组 ──
                "group_score": group_score,
                "group_rank": group_rank,
                "is_selected_group": is_selected_group,
                "is_extrapolated": rec["_is_extrapolated"],
                "fib_score": rec["_fib_score"],
            })

    # ── G 组: 方案 1 触碰波段划分与局部极值 (Peak NMS) 打标 ──
    tagged_out = _tag_episodes(out, max_gap_days=4)
    peak_count = sum(1 for r in tagged_out if r.get("is_peak_touch", False))
    log.info(f'[price_touch] 候选 {len(tagged_out)} 条 (其中峰值确认点 {peak_count} 条, 扫描 {n - start} bars)')
    return {
        "candidates": tagged_out,
        "summary": {
            "total_candidates": len(tagged_out),
            "peak_candidates": peak_count,
        },
    }


def _tag_episodes(cands: list[dict], max_gap_days: int = 4) -> list[dict]:
    if not cands:
        return cands
    df = pd.DataFrame(cands)
    max_gap_ms = max_gap_days * 86400 * 1000
    df = df.sort_values(["fib_group_id", "ratio", "ts"]).reset_index(drop=True)
    prev_ts = df.groupby(["fib_group_id", "ratio"])["ts"].shift(1)
    is_new = (prev_ts.isna()) | ((df["ts"] - prev_ts) > max_gap_ms)
    df["episode_id"] = is_new.cumsum().astype(int)
    df["touch_seq"] = (df.groupby(["fib_group_id", "ratio", "episode_id"]).cumcount() + 1).astype(int)
    df["is_first_touch"] = df["touch_seq"] == 1

    # candidate_score 优先，其次 proximity
    df["_sort_key"] = df["candidate_score"].fillna(0.0) * 100.0 + df["proximity"].fillna(0.0)
    peak_idx = df.groupby(["fib_group_id", "ratio", "episode_id"])["_sort_key"].idxmax()
    df["is_peak_touch"] = False
    df.loc[peak_idx, "is_peak_touch"] = True
    df = df.drop(columns=["_sort_key"])
    return df.to_dict("records")
