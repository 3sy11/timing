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
    "ts", "close", "approach", "ratio", "level_price",
    "fib_group_id", "multiplier", "direction",
    "effective_ts", "invalidated_ts", "compute_id", "analysis_id",
    "pre_test_n", "pre_hold_n", "pre_hold_rate", "pre_weak_share",
    "fit_test_n", "fit_hold_n", "fit_hold_rate", "fit_weak_share",
    "nearby_fib_n", "nearby_fib_hold_n", "nearby_fib_hold_rate",
    "nearby_fib_weak_n", "nearby_fib_weak_share",
    "nearby_cluster_n", "nearby_cluster_hold_n", "nearby_cluster_hold_rate",
    "nearby_cluster_weak_n", "nearby_cluster_weak_share",
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
    analysis_id: str = "",
) -> dict:
    cfg = cfg or PriceTouchConfig()
    empty = {"candidates": [], "summary": {"total_candidates": 0}}
    if klines_df.empty or result_df.empty:
        return empty

    kdf = klines_df.sort_values("ts").reset_index(drop=True)
    ts = kdf["ts"].to_numpy(dtype=np.int64)
    highs = kdf["high"].to_numpy(dtype=np.float64)
    lows = kdf["low"].to_numpy(dtype=np.float64)
    closes = kdf["close"].to_numpy(dtype=np.float64)
    n = len(kdf)
    start = max(0, n - int(cfg.scan_bars)) if int(cfg.scan_bars) > 0 else 0
    pk = float(cfg.proximity_k)
    nk = float(cfg.neighbor_k)

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
            out.append({
                "ts": t, "close": close, "approach": approach,
                "ratio": rec["_ratio"], "level_price": p0,
                "fib_group_id": rec["fib_group_id"],
                "multiplier": rec["_mult"], "direction": rec["_dir"],
                "effective_ts": rec["_eff"],
                "invalidated_ts": None if not _is_num(inv) else int(inv),
                "compute_id": compute_id, "analysis_id": analysis_id,
                "pre_test_n": pre["test_n"], "pre_hold_n": pre["hold_n"],
                "pre_hold_rate": pre["hold_rate"], "pre_weak_share": pre["weak_share"],
                "fit_test_n": fit["test_n"], "fit_hold_n": fit["hold_n"],
                "fit_hold_rate": fit["hold_rate"], "fit_weak_share": fit["weak_share"],
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
            })

    log.info(f'[price_touch] 候选 {len(out)} 条 (扫描 {n - start} bars)')
    return {
        "candidates": out,
        "summary": {"total_candidates": len(out)},
    }
