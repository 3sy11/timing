"""fib_retracement pipeline — 6 组独立生命周期管理。
核心逻辑: 窗口内聚类 → fit_fib_grid_to_clusters → 内层6线(含0%)对齐聚类 → 100%数学扩展。
每组 (mult, dir) 独立追踪：boundary_break → 重算过 min_fit_score 则上线，否则空置。
最终交付: result.parquet 以 PriceLine 为维度打平，每行一条 fib level。
"""
import json, logging
from typing import List, Optional, Set, Tuple
import pandas as pd
from .algo import (base_df, tag_pivots, tag_zigzag, tag_regression, compute_confidence,
                   cluster_prices, adaptive_window_start,
                   fit_fib_grid_to_clusters, levels_from_hl)
from .config import RetracementConfig
from ...writer import StepWriter

log = logging.getLogger(__name__)
MAX_CANDIDATES = 6  # 每次 fit 最多返回的候选组数


def _compute_fib_at(feature_df, end_idx: int, cfg,
                    target_keys: Optional[Set[Tuple[int, str]]] = None) -> list:
    """在 feature_df[:end_idx] 上用窗口内聚类 fit fib grid。"""
    effective_df = feature_df.iloc[:end_idx]
    effective_ts = int(effective_df.iloc[-1]["ts"])
    records = []
    for mult in (1, 2, 3):
        if target_keys and not any(k[0] == mult for k in target_keys):
            continue
        target_bars = cfg.recent_bars * mult
        actual_start = adaptive_window_start(effective_df, target_bars, min_conf=cfg.min_cluster_conf)
        recent_df = effective_df.iloc[actual_start:].reset_index(drop=True)
        if len(recent_df) < 10:
            continue
        win_ch = cluster_prices(recent_df, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        win_cl = cluster_prices(recent_df, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        centers = []
        if not win_ch.empty:
            centers.extend(list(zip(win_ch["center"].tolist(), win_ch["total_conf"].tolist())))
        if not win_cl.empty:
            centers.extend(list(zip(win_cl["center"].tolist(), win_cl["total_conf"].tolist())))
        centers.sort(key=lambda x: x[0])
        if len(centers) < 3:
            continue
        for direction in ("up", "down"):
            if target_keys and (mult, direction) not in target_keys:
                continue
            fits = fit_fib_grid_to_clusters(centers, direction,
                                           min_span_pct=cfg.min_leg_span_pct, min_assigned=2)
            if not fits:
                continue
            leg_start_ts = int(recent_df.iloc[0]["ts"])
            leg_end_ts = int(recent_df.iloc[-1]["ts"])
            for fit in fits[:MAX_CANDIDATES]:
                h, l, score = fit["high"], fit["low"], fit["score"]
                if score < cfg.min_fit_score:
                    continue
                levels = levels_from_hl(h, l, direction)
                records.append({"effective_ts": effective_ts, "multiplier": mult,
                               "direction": direction, "score": score,
                               "leg_start_ts": leg_start_ts, "leg_end_ts": leg_end_ts,
                               "leg_low": l, "leg_high": h,
                               "levels_json": json.dumps(levels),
                               "invalidated_ts": None, "invalidate_reason": None})
    return records


def _flatten_to_lines(fib_records: List[dict]) -> pd.DataFrame:
    """将 fib 组列表打平为 PriceLine 维度的表，每行一条 fib level。"""
    rows = []
    for rec in fib_records:
        levels = json.loads(rec["levels_json"]) if isinstance(rec["levels_json"], str) else rec["levels_json"]
        for ratio, price in levels:
            rows.append({
                "effective_ts": rec["effective_ts"],
                "multiplier": rec["multiplier"],
                "direction": rec["direction"],
                "fib_score": rec["score"],
                "leg_low": rec["leg_low"],
                "leg_high": rec["leg_high"],
                "leg_start_ts": rec["leg_start_ts"],
                "leg_end_ts": rec["leg_end_ts"],
                "invalidated_ts": rec.get("invalidated_ts"),
                "invalidate_reason": rec.get("invalidate_reason"),
                "source": rec.get("source"),
                "ratio": round(ratio, 4),
                "price": round(price, 2),
                "is_extrapolated": abs(ratio - 1.0) < 0.001,
            })
    if not rows:
        return pd.DataFrame(columns=["effective_ts", "multiplier", "direction", "fib_score",
                                     "leg_low", "leg_high", "leg_start_ts", "leg_end_ts",
                                     "invalidated_ts", "invalidate_reason", "source",
                                     "ratio", "price", "is_extrapolated"])
    return pd.DataFrame(rows)


def run_pipeline(klines: List[dict], cfg: RetracementConfig, writer: StepWriter) -> dict:
    """执行 fib_retracement 全管道。返回摘要。"""
    cfg = cfg or RetracementConfig()

    # ── step1: pivots + zigzag + regression ──
    feature_df = base_df(klines)
    feature_df, w1 = tag_pivots(feature_df, cfg.pivot_windows)
    feature_df, w2 = tag_zigzag(feature_df, cfg.zigzag_thresholds)
    feature_df, w3 = tag_regression(feature_df, cfg.regression_windows)
    wmap = {**w1, **w2, **w3}
    step1_cols = ["ts", "open", "high", "low", "close", "volume"] + list(wmap.keys())
    step1_df = feature_df[[c for c in step1_cols if c in feature_df.columns]].copy()
    writer.write_step("step1_pivots", step1_df)

    # ── step2: confidence ──
    feature_df = compute_confidence(feature_df, wmap, cfg.weights)
    step2_df = feature_df[["ts", "high", "low", "close", "conf_high", "conf_low"]].copy()
    writer.write_step("step2_confidence", step2_df)

    # ── step3: 6 组独立生命周期管理 (fib 组表) ──
    n = len(feature_df)
    skip_recent = cfg.skip_recent
    start_pos = max(cfg.min_bars, cfg.recent_bars * 3)
    end_pos = max(0, n - skip_recent)

    if start_pos >= end_pos:
        log.warning(f'[fib_retracement] 数据不足: n={n} start_pos={start_pos} end_pos={end_pos}')
        writer.write_step("step3_fib_groups", pd.DataFrame())
        writer.write_result(pd.DataFrame())
        return {"klines": n, "result_rows": 0, "invalidations": 0}

    closes = feature_df["close"].tolist()
    ts_list = feature_df["ts"].tolist()
    break_bars = cfg.invalidate_break_bars
    boundary_tol_k = cfg.get("boundary_tolerance_k", 0.05)
    vacancy_interval = cfg.get("vacancy_retry_interval", 5)

    ALL_KEYS = [(m, d) for m in (1, 2, 3) for d in ("up", "down")]
    MAX_ACTIVE = 3
    VACANCY_INTERVAL = max(vacancy_interval, 20)  # 防止过于频繁

    active_slots: dict[tuple, list] = {key: [] for key in ALL_KEYS}
    break_counts_slots: dict[tuple, list] = {key: [] for key in ALL_KEYS}
    vacancy_counters: dict[tuple, int] = {key: 0 for key in ALL_KEYS}

    def _no_duplicate(new_rec, existing_list):
        """检查新组与已有组不完全相同 (leg_high/leg_low 差异 > 1%)"""
        nh, nl = new_rec["leg_high"], new_rec["leg_low"]
        span = nh - nl
        if span <= 0:
            return False
        for ex in existing_list:
            if abs(nh - ex["leg_high"]) / span < 0.01 and abs(nl - ex["leg_low"]) / span < 0.01:
                return False
        return True

    def _try_fill_slots(key, bi, source, parent_ts=None):
        """尝试在 key 的槽位中填入新的组，直到 MAX_ACTIVE"""
        slots = active_slots[key]
        bc = break_counts_slots[key]
        if len(slots) >= MAX_ACTIVE:
            return
        recs = _compute_fib_at(feature_df, bi, cfg, target_keys={key})
        for r in recs:
            if r["score"] < cfg.min_fit_score:
                continue
            if not _no_duplicate(r, slots):
                continue
            r["source"] = source
            r["parent_eff_ts"] = parent_ts
            slots.append(r)
            bc.append(0)
            if len(slots) >= MAX_ACTIVE:
                break

    # 初始化：尽量为每个 key 填满 3 个组
    log.info(f'[fib_retracement] 生命周期: start={start_pos} end={end_pos} break_bars={break_bars} tol_k={boundary_tol_k} max_active={MAX_ACTIVE}')
    for key in ALL_KEYS:
        _try_fill_slots(key, start_pos, "initial")

    all_records = []
    invalidation_count = 0

    for bi in range(start_pos + 1, end_pos + 1):
        close = closes[bi]
        for key in ALL_KEYS:
            slots = active_slots[key]
            bc = break_counts_slots[key]
            to_remove = []
            for si in range(len(slots)):
                rec = slots[si]
                span = rec["leg_high"] - rec["leg_low"]
                buffer = span * boundary_tol_k
                if close > rec["leg_high"] + buffer or close < rec["leg_low"] - buffer:
                    bc[si] += 1
                    if bc[si] >= break_bars:
                        rec["invalidated_ts"] = int(ts_list[bi])
                        rec["invalidate_reason"] = "boundary_break"
                        all_records.append(rec)
                        invalidation_count += 1
                        to_remove.append(si)
                else:
                    bc[si] = 0
            for si in sorted(to_remove, reverse=True):
                slots.pop(si)
                bc.pop(si)
            # 有失效则立即尝试填充
            if to_remove and len(slots) < MAX_ACTIVE:
                parent = all_records[-1]["effective_ts"] if all_records else None
                _try_fill_slots(key, bi, "event_break", parent)
            # 空位定期尝试填充
            elif len(slots) < MAX_ACTIVE:
                vacancy_counters[key] += 1
                if vacancy_counters[key] >= VACANCY_INTERVAL:
                    vacancy_counters[key] = 0
                    _try_fill_slots(key, bi, "vacancy_fill")

    for key in ALL_KEYS:
        for rec in active_slots[key]:
            all_records.append(rec)

    # 写 step3: fib 组表 (中间产物)
    step3_cols = ["effective_ts", "multiplier", "direction", "score", "leg_start_ts", "leg_end_ts",
                  "leg_low", "leg_high", "levels_json", "invalidated_ts", "invalidate_reason",
                  "source", "parent_eff_ts"]
    step3_df = pd.DataFrame(all_records, columns=step3_cols) if all_records else pd.DataFrame(columns=step3_cols)
    writer.write_step("step3_fib_groups", step3_df)

    # 写 result: 打平为 PriceLine 维度
    result_df = _flatten_to_lines(all_records)
    writer.write_result(result_df)

    n_lines = len(result_df)
    log.info(f'[fib_retracement] 完成: klines={n} fib_groups={len(step3_df)} lines={n_lines} invalidations={invalidation_count}')
    return {"klines": n, "fib_groups": len(step3_df), "result_rows": n_lines, "invalidations": invalidation_count}
