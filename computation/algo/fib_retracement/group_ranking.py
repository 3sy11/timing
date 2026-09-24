"""仍有效 Fib 组的组级评分。分数在组生效时即冻结，只在数据末尾仍有效的组里排名。"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .line_events import fib_group_id

RANK_COLS = [
    "compute_id", "symbol", "interval", "fib_group_id",
    "effective_ts", "multiplier", "direction", "leg_low", "leg_high", "leg_start_ts",
    "fib_score",
    "pre_bounce_n", "pre_break_n", "pre_weak_n", "pre_test_n", "pre_hold_n",
    "pre_hold_rate", "pre_weak_share", "pre_sample_conf",
    "fit_bounce_n", "fit_break_n", "fit_weak_n", "fit_test_n", "fit_hold_n",
    "fit_hold_rate", "fit_weak_share", "fit_sample_conf",
    "align_ratio", "touched_inner_n", "touched_ratio",
    "ext0_touched", "ext1_touched", "ext_touched_n",
    "score_prefit", "score_fitwin", "score_structure", "score_activity", "score_extrapolated",
    "group_score", "rank", "is_selected",
]

_INNER = {0.236, 0.382, 0.5, 0.618, 0.786}
_RANK_DEFAULTS = {
    "rank_top_n": 3,
    "rank_w_prefit": 3.0,
    "rank_w_fitwin": 1.0,
    "rank_w_structure": 1.0,
    "rank_w_activity": 1.0,
    "rank_w_extrapolated": 0.5,
    "rank_sample_cap": 8,
    "rank_structure_cap": 5.0,
}


def _cfg(cfg: Any, key: str):
    if cfg is None:
        return _RANK_DEFAULTS[key]
    if isinstance(cfg, dict) and key in cfg:
        return cfg[key]
    return getattr(cfg, key, _RANK_DEFAULTS[key])


def _ratio(v) -> float:
    return round(float(v), 4)


def _alive(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except TypeError:
        return False


def _counts(outcomes: list[str]) -> dict:
    b = sum(1 for o in outcomes if o == "bounce")
    k = sum(1 for o in outcomes if o == "break")
    w = sum(1 for o in outcomes if o == "weak")
    test_n = b + k + w
    hold_n = b + k
    return {
        "bounce_n": b,
        "break_n": k,
        "weak_n": w,
        "test_n": test_n,
        "hold_n": hold_n,
        "hold_rate": round(b / hold_n, 4) if hold_n else None,
        "weak_share": round(w / test_n, 4) if test_n else None,
    }


def build_group_ranking(
    result_df: pd.DataFrame,
    events_df: pd.DataFrame,
    cfg: Any = None,
    compute_id: str = "",
    symbol: str = "",
    interval: str = "",
) -> pd.DataFrame:
    empty = pd.DataFrame(columns=RANK_COLS)
    if result_df is None or result_df.empty:
        return empty

    top_n = int(_cfg(cfg, "rank_top_n"))
    w_pre = float(_cfg(cfg, "rank_w_prefit"))
    w_fit = float(_cfg(cfg, "rank_w_fitwin"))
    w_st = float(_cfg(cfg, "rank_w_structure"))
    w_act = float(_cfg(cfg, "rank_w_activity"))
    w_ext = float(_cfg(cfg, "rank_w_extrapolated"))
    cap = max(int(_cfg(cfg, "rank_sample_cap")), 1)
    st_cap = float(_cfg(cfg, "rank_structure_cap")) or 1.0

    work = result_df.copy()
    if "fib_group_id" not in work.columns:
        work["fib_group_id"] = [
            fib_group_id(r.effective_ts, r.multiplier, r.direction, r.leg_low, r.leg_high)
            for r in work.itertuples(index=False)
        ]
    work = work[work["invalidated_ts"].map(_alive)]
    if work.empty:
        return empty

    ev = events_df if events_df is not None else pd.DataFrame()
    if not ev.empty and "target_kind" in ev.columns:
        ev = ev[ev["target_kind"].astype(str) == "fib"]
    by_group: dict[str, pd.DataFrame] = {}
    if not ev.empty and "fib_group_id" in ev.columns:
        for gid, part in ev.groupby("fib_group_id"):
            by_group[str(gid)] = part

    rows = []
    for gid, g in work.groupby("fib_group_id"):
        head = g.iloc[0]
        events = by_group.get(str(gid), pd.DataFrame())
        pre = fit = []
        touched = set()
        if not events.empty:
            pre = events.loc[events["period"] == "prefit", "outcome_atr"].astype(str).tolist()
            fit = events.loc[events["period"] == "fitwin", "outcome_atr"].astype(str).tolist()
            touched = {_ratio(r) for r in events["ratio"].tolist()}
        pc, fc = _counts(pre), _counts(fit)
        pre_conf = min(pc["hold_n"], cap) / cap
        fit_conf = min(fc["hold_n"], cap) / cap
        inner = g[g["ratio"].map(lambda x: _ratio(x) in _INNER)]
        aligned = 0
        if len(inner):
            aligned = int(inner["is_cluster_aligned"].map(lambda v: bool(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else False).sum())
        align_ratio = round(aligned / 5, 4)
        touched_inner = len(touched & _INNER)
        touched_ratio = round(touched_inner / 5, 4)
        ext0 = 0.0 in touched
        ext1 = 1.0 in touched
        ext_n = int(ext0) + int(ext1)
        fib_score = float(head["fib_score"])
        score_pre = round(w_pre * (pc["hold_rate"] or 0.0) * pre_conf, 4)
        score_fit = round(w_fit * (fc["hold_rate"] or 0.0) * fit_conf, 4)
        score_st = round(w_st * min(fib_score, st_cap) / st_cap * align_ratio, 4)
        score_act = round(w_act * touched_ratio, 4)
        score_ext = round(w_ext * ext_n / 2, 4)
        cid = str(head["compute_id"]) if "compute_id" in g.columns and pd.notna(head.get("compute_id")) else compute_id
        sym = str(head["symbol"]) if "symbol" in g.columns and pd.notna(head.get("symbol")) else symbol
        itv = str(head["interval"]) if "interval" in g.columns and pd.notna(head.get("interval")) else interval
        rows.append({
            "compute_id": cid, "symbol": sym, "interval": itv, "fib_group_id": str(gid),
            "effective_ts": int(head["effective_ts"]),
            "multiplier": int(head["multiplier"]),
            "direction": str(head["direction"]),
            "leg_low": float(head["leg_low"]),
            "leg_high": float(head["leg_high"]),
            "leg_start_ts": int(head["leg_start_ts"]),
            "fib_score": round(fib_score, 4),
            "pre_bounce_n": pc["bounce_n"], "pre_break_n": pc["break_n"], "pre_weak_n": pc["weak_n"],
            "pre_test_n": pc["test_n"], "pre_hold_n": pc["hold_n"],
            "pre_hold_rate": pc["hold_rate"], "pre_weak_share": pc["weak_share"],
            "pre_sample_conf": round(pre_conf, 4),
            "fit_bounce_n": fc["bounce_n"], "fit_break_n": fc["break_n"], "fit_weak_n": fc["weak_n"],
            "fit_test_n": fc["test_n"], "fit_hold_n": fc["hold_n"],
            "fit_hold_rate": fc["hold_rate"], "fit_weak_share": fc["weak_share"],
            "fit_sample_conf": round(fit_conf, 4),
            "align_ratio": align_ratio,
            "touched_inner_n": touched_inner,
            "touched_ratio": touched_ratio,
            "ext0_touched": ext0, "ext1_touched": ext1, "ext_touched_n": ext_n,
            "score_prefit": score_pre, "score_fitwin": score_fit,
            "score_structure": score_st, "score_activity": score_act, "score_extrapolated": score_ext,
            "group_score": round(score_pre + score_fit + score_st + score_act + score_ext, 4),
        })

    out = pd.DataFrame(rows)
    out = out.sort_values(
        ["group_score", "effective_ts", "fib_group_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    out["rank"] = out.index + 1
    out["is_selected"] = out["rank"] <= top_n
    return out[RANK_COLS]
