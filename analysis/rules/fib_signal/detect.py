"""fib_signal：硬过滤 + 分项分。不写多空，不算出场、止损、过期。"""
from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

from computation.algo.fib_retracement.line_events import fib_group_id
from .config import FibSignalConfig

log = logging.getLogger(__name__)

SIGNAL_COLS = [
    "signal_id", "compute_id", "feature_id", "symbol", "interval", "ts",
    "fib_group_id", "ratio", "level_price", "multiplier", "direction",
    "atr", "distance_atr", "episode_max_dist_atr",
    "period", "proximity", "candidate_score", "line_quality",
    "episode_id", "touch_seq", "is_first_touch", "is_peak_touch",
    "signal_score",
    "pass_hard_filter", "filter_fail_reason",
    "score_pre_hold", "score_pre_weak", "score_fit_hold",
    "score_cluster", "score_cluster_conf",
    "score_nearby_fib", "score_nearby_frate", "score_nearby_cls",
    "score_subtotal",
    "is_cluster_aligned", "fib_score", "is_extrapolated", "cluster_distance_ratio",
]


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    return x


def _r4(v: float) -> float:
    return round(float(v), 4)


def _fail_reason(row: dict, ent: dict, cfg: FibSignalConfig) -> Optional[str]:
    hold_n = int(row.get("pre_hold_n") or 0)
    if hold_n < int(cfg.min_pre_hold_n):
        return f"pre_hold_n<{int(cfg.min_pre_hold_n)}"
    rate = _num(row.get("pre_hold_rate"))
    if rate is None:
        return "pre_hold_rate_null"
    if rate < float(cfg.min_pre_hold_rate):
        return f"pre_hold_rate<{cfg.min_pre_hold_rate}"
    weak = _num(row.get("pre_weak_share"))
    if weak is not None and weak > float(cfg.max_pre_weak_share):
        return f"pre_weak_share>{cfg.max_pre_weak_share}"
    if ent is None:
        return "result_miss"
    extrapolated = bool(ent["is_extrapolated"])
    if (not cfg.allow_extrapolated) and extrapolated:
        return "extrapolated"
    if cfg.require_cluster_aligned and (not bool(ent["is_cluster_aligned"])):
        return "not_cluster_aligned"
    return None


def _index_result(result_df: pd.DataFrame) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    rows = result_df.to_dict("records")
    by_key = {}
    effs, invs, confs = [], [], []
    for rec in rows:
        gid = fib_group_id(
            rec["effective_ts"], rec["multiplier"], rec["direction"],
            rec["leg_low"], rec["leg_high"],
        )
        key = (gid, round(float(rec["ratio"]), 4))
        extrapolated = rec.get("is_extrapolated")
        if extrapolated is None or (isinstance(extrapolated, float) and math.isnan(extrapolated)):
            extrapolated = round(float(rec["ratio"]), 4) in (0.0, 1.0)
        inv = rec.get("invalidated_ts")
        inv_i = -1 if _num(inv) is None else int(inv)
        ent = {
            "is_extrapolated": bool(extrapolated),
            "is_cluster_aligned": bool(rec.get("is_cluster_aligned")),
            "fib_score": _num(rec.get("fib_score")),
            "cluster_distance_ratio": _num(rec.get("cluster_distance_ratio")),
            "nearest_cluster_conf": _num(rec.get("nearest_cluster_conf")),
        }
        by_key[key] = ent
        effs.append(int(rec["effective_ts"]))
        invs.append(inv_i)
        confs.append(ent["nearest_cluster_conf"] if ent["nearest_cluster_conf"] is not None else np.nan)
    return (
        by_key,
        np.array(effs, dtype=np.int64),
        np.array(invs, dtype=np.int64),
        np.array(confs, dtype=np.float64),
    )


def _conf_span(effs, invs, confs, ts_values: np.ndarray) -> dict[int, tuple[float, float] | None]:
    out = {}
    for t in ts_values:
        t = int(t)
        alive = (effs <= t) & ((invs < 0) | (t < invs))
        vals = confs[alive]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0 or float(vals.max()) == float(vals.min()):
            out[t] = None
        else:
            out[t] = (float(vals.min()), float(vals.max()))
    return out


def _scores(row: dict, ent: dict, span, cfg: FibSignalConfig) -> dict:
    pre_hold = _num(row.get("pre_hold_rate"))
    pre_weak = _num(row.get("pre_weak_share"))
    fit_hold = _num(row.get("fit_hold_rate"))
    nb_rate = _num(row.get("nearby_fib_hold_rate"))
    fib_n = int(row.get("nearby_fib_n") or 0)
    cls_n = int(row.get("nearby_cluster_n") or 0)
    aligned = bool(ent["is_cluster_aligned"]) if ent else False
    conf = ent["nearest_cluster_conf"] if ent else None
    if conf is not None and span is not None:
        lo, hi = span
        conf_norm = (conf - lo) / (hi - lo)
    else:
        conf_norm = 0.0
    fib_cap = max(int(cfg.nearby_fib_cap), 1)
    cls_cap = max(int(cfg.nearby_cls_cap), 1)
    parts = {
        "score_pre_hold": _r4(float(cfg.w_pre_hold) * (pre_hold or 0.0)),
        "score_pre_weak": _r4(-(float(cfg.w_pre_weak) * (pre_weak or 0.0))),
        "score_fit_hold": _r4(float(cfg.w_fit_hold) * (fit_hold or 0.0)),
        "score_cluster": _r4(float(cfg.w_cluster) * (1.0 if aligned else 0.0)),
        "score_cluster_conf": _r4(float(cfg.w_cluster_conf) * conf_norm),
        "score_nearby_fib": _r4(float(cfg.w_nearby_fib) * min(fib_n, fib_cap) / fib_cap),
        "score_nearby_frate": _r4(float(cfg.w_nearby_frate) * (nb_rate or 0.0)),
        "score_nearby_cls": _r4(float(cfg.w_nearby_cls) * min(cls_n, cls_cap) / cls_cap),
    }
    parts["score_subtotal"] = _r4(sum(parts.values()))
    return parts


def run_detection(
    candidates_df: pd.DataFrame,
    result_df: pd.DataFrame,
    cfg: FibSignalConfig = None,
    signal_id: str = "",
    symbol: str = "",
    interval: str = "",
) -> dict:
    cfg = cfg or FibSignalConfig()
    empty = {"signals": [], "summary": {"total_signals": 0, "candidates": 0}}
    if candidates_df.empty or result_df.empty:
        return empty

    by_key, effs, invs, confs = _index_result(result_df)
    ts_values = candidates_df["ts"].dropna().unique()
    spans = _conf_span(effs, invs, confs, ts_values)

    out = []
    fail_n = 0
    low_n = 0
    for rec in candidates_df.to_dict("records"):
        key = (str(rec["fib_group_id"]), round(float(rec["ratio"]), 4))
        ent = by_key.get(key)
        reason = _fail_reason(rec, ent, cfg)
        passed = reason is None
        parts = _scores(rec, ent, spans.get(int(rec["ts"])), cfg)
        score = parts["score_subtotal"]
        if not passed:
            fail_n += 1
        elif score < float(cfg.min_signal_score):
            low_n += 1
        keep = passed and score >= float(cfg.min_signal_score)
        if (not keep) and (not cfg.debug_mode):
            continue
        row = {
            "signal_id": signal_id,
            "compute_id": rec.get("compute_id") or "",
            "feature_id": rec.get("feature_id") or "",
            "symbol": symbol,
            "interval": interval,
            "ts": int(rec["ts"]),
            "fib_group_id": str(rec["fib_group_id"]),
            "ratio": float(rec["ratio"]),
            "level_price": float(rec["level_price"]),
            "multiplier": int(rec["multiplier"]),
            "direction": str(rec["direction"]),
            "atr": _num(rec.get("atr")),
            "distance_atr": _num(rec.get("distance_atr")),
            "episode_max_dist_atr": _num(rec.get("episode_max_dist_atr")),
            "period": rec.get("period", "predict"),
            "proximity": rec.get("proximity"),
            "candidate_score": rec.get("candidate_score"),
            "line_quality": rec.get("line_quality"),
            "episode_id": int(rec["episode_id"]) if "episode_id" in rec and rec["episode_id"] is not None else None,
            "touch_seq": int(rec["touch_seq"]) if "touch_seq" in rec and rec["touch_seq"] is not None else None,
            "is_first_touch": bool(rec.get("is_first_touch", True)),
            "is_peak_touch": bool(rec.get("is_peak_touch", True)),
            "signal_score": score,
            "pass_hard_filter": passed,
            "filter_fail_reason": None if passed else reason,
            **parts,
            "is_cluster_aligned": bool(ent["is_cluster_aligned"]) if ent else False,
            "fib_score": ent["fib_score"] if ent else None,
            "is_extrapolated": bool(ent["is_extrapolated"]) if ent else False,
            "cluster_distance_ratio": ent["cluster_distance_ratio"] if ent else None,
        }
        out.append(row)

    log.info(
        f'[fib_signal] 候选 {len(candidates_df)} → 写出 {len(out)} '
        f'(硬过滤未过 {fail_n}, 低分 {low_n}, debug={bool(cfg.debug_mode)})'
    )
    return {
        "signals": out,
        "summary": {
            "total_signals": len(out),
            "candidates": int(len(candidates_df)),
            "hard_fail": fail_n,
            "low_score": low_n,
        },
    }
