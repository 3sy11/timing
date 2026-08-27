"""fib_retracement pipeline — 6 组独立生命周期管理。
核心逻辑: 窗口内聚类 → fit_fib_grid_to_clusters → 内层5线对齐聚类 → 0%/100%数学扩展。
每组 (mult, dir) 独立追踪：boundary_break(3 bar) 无条件杀死 → 重算过 min_fit_score 则上线，否则空置。
"""
import json, logging
from dataclasses import asdict
from typing import List, Optional, Set, Tuple
import pandas as pd
from .algo import (base_df, tag_pivots, tag_zigzag, tag_regression, compute_confidence,
                   cluster_prices, extract_trend_legs, score_and_rank,
                   adaptive_window_start, merge_legs_weighted, fit_fib_groups,
                   fit_fib_grid_to_clusters, levels_from_hl)
from .config import RetracementConfig
from .models import TrendLeg
from ...writer import StepWriter

log = logging.getLogger(__name__)


def _compute_fib_at(feature_df, end_idx: int, cfg,
                    target_keys: Optional[Set[Tuple[int, str]]] = None,
                    clusters_high_df=None, clusters_low_df=None) -> list:
    """在 feature_df[:end_idx] 上用窗口内聚类 fit fib grid（自然扩展 0%/100%）。
    核心逻辑：对窗口内聚类做 fib grid fitting，内层5线对齐聚类，0%/100%是数学扩展。
    target_keys: 只计算指定的 (mult, direction)，None 表示全量。
    """
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
        # 用窗口内聚类（关键：只用当前窗口的数据，而非全历史）
        win_ch = cluster_prices(recent_df, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        win_cl = cluster_prices(recent_df, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        # 合并高低聚类中心
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
            # fit_fib_grid_to_clusters: 从聚类反推 (high, low)，使内层5线对齐聚类
            fits = fit_fib_grid_to_clusters(centers, direction,
                                           min_span_pct=cfg.min_leg_span_pct, min_assigned=2)
            if not fits:
                continue
            best = fits[0]
            h, l, score = best["high"], best["low"], best["score"]
            if score < cfg.min_fit_score:
                continue
            levels = levels_from_hl(h, l, direction)
            # 取窗口的起止 ts 作为 leg 时间范围
            leg_start_ts = int(recent_df.iloc[0]["ts"])
            leg_end_ts = int(recent_df.iloc[-1]["ts"])
            records.append({"effective_ts": effective_ts, "multiplier": mult,
                           "direction": direction, "score": score,
                           "leg_start_ts": leg_start_ts, "leg_end_ts": leg_end_ts,
                           "leg_low": l, "leg_high": h,
                           "levels_json": json.dumps(levels),
                           "invalidated_ts": None, "invalidate_reason": None})
    return records


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

    # ── step3: clusters ──
    clusters_high_df = cluster_prices(feature_df, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    clusters_low_df = cluster_prices(feature_df, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
    step3_df = pd.concat([clusters_high_df, clusters_low_df], ignore_index=True) if not (clusters_high_df.empty and clusters_low_df.empty) else pd.DataFrame(columns=["kind", "center", "hit_count", "total_conf", "last_index", "last_ts"])
    writer.write_step("step3_clusters", step3_df)

    # ── step4 + result: 6 组独立生命周期管理 ──
    n = len(feature_df)
    skip_recent = cfg.skip_recent
    start_pos = max(cfg.min_bars, cfg.recent_bars * 3)
    end_pos = max(0, n - skip_recent)

    if start_pos >= end_pos:
        log.warning(f'[fib_retracement] 数据不足: n={n} start_pos={start_pos} end_pos={end_pos}')
        writer.write_step("step4_legs", pd.DataFrame())
        writer.write_result(pd.DataFrame())
        return {"klines": n, "result_rows": 0, "invalidations": 0}

    closes = feature_df["close"].tolist()
    ts_list = feature_df["ts"].tolist()
    break_bars = cfg.invalidate_break_bars
    vacancy_interval = cfg.get("vacancy_retry_interval", 5)

    ALL_KEYS = [(m, d) for m in (1, 2, 3) for d in ("up", "down")]

    # 初始化 6 组
    active: dict[tuple, dict | None] = {}
    break_counts: dict[tuple, int] = {}
    vacancy_counters: dict[tuple, int] = {}

    for key in ALL_KEYS:
        recs = _compute_fib_at(feature_df, start_pos, cfg, target_keys={key})
        if recs and recs[0]["score"] >= cfg.min_fit_score:
            recs[0]["source"] = "initial"
            recs[0]["parent_eff_ts"] = None
            active[key] = recs[0]
        else:
            active[key] = None
        break_counts[key] = 0
        vacancy_counters[key] = 0

    all_records = []
    invalidation_count = 0

    log.info(f'[fib_retracement] 独立生命周期: start_pos={start_pos} end_pos={end_pos} break_bars={break_bars}')

    # 逐 bar 推进
    for bi in range(start_pos + 1, end_pos + 1):
        close = closes[bi]
        for key in ALL_KEYS:
            rec = active[key]
            if rec is not None:
                # 活跃组：检测 boundary_break
                if close > rec["leg_high"] or close < rec["leg_low"]:
                    break_counts[key] += 1
                    if break_counts[key] >= break_bars:
                        # 老组死亡
                        rec["invalidated_ts"] = int(ts_list[bi])
                        rec["invalidate_reason"] = "boundary_break"
                        all_records.append(rec)
                        invalidation_count += 1
                        # 重算
                        new_recs = _compute_fib_at(feature_df, bi, cfg, target_keys={key})
                        if new_recs and new_recs[0]["score"] >= cfg.min_fit_score:
                            new_recs[0]["source"] = "event_break"
                            new_recs[0]["parent_eff_ts"] = rec["effective_ts"]
                            active[key] = new_recs[0]
                        else:
                            active[key] = None
                            vacancy_counters[key] = 0
                        break_counts[key] = 0
                else:
                    break_counts[key] = 0
            else:
                # 空置槽位：按间隔重试
                vacancy_counters[key] += 1
                if vacancy_counters[key] >= vacancy_interval:
                    vacancy_counters[key] = 0
                    new_recs = _compute_fib_at(feature_df, bi, cfg, target_keys={key})
                    if new_recs and new_recs[0]["score"] >= cfg.min_fit_score:
                        new_recs[0]["source"] = "vacancy_fill"
                        new_recs[0]["parent_eff_ts"] = None
                        active[key] = new_recs[0]
                        break_counts[key] = 0

    # 将仍活跃的组写入（未失效）
    for key in ALL_KEYS:
        rec = active[key]
        if rec is not None:
            all_records.append(rec)

    # 写入 step4_legs（兼容）
    effective_end = max(0, n - skip_recent)
    effective_df = feature_df.iloc[:effective_end]
    all_legs_records = []
    for mult in (1, 2, 3):
        target_bars = cfg.recent_bars * mult
        actual_start = adaptive_window_start(effective_df, target_bars, min_conf=cfg.min_cluster_conf)
        recent_df = effective_df.iloc[actual_start:].reset_index(drop=True)
        legs = extract_trend_legs(recent_df, clusters_high_df, clusters_low_df, min_span_pct=cfg.min_leg_span_pct)
        ranked = score_and_rank(legs, top_n=cfg.top_n, total_bars=len(recent_df))
        for lg in ranked:
            rec = asdict(lg)
            rec["multiplier"] = mult
            all_legs_records.append(rec)
    step4_df = pd.DataFrame(all_legs_records) if all_legs_records else pd.DataFrame(columns=["start_idx", "end_idx", "start_ts", "end_ts", "low", "high", "direction", "span_pct", "conf_score", "multiplier"])
    writer.write_step("step4_legs", step4_df)

    # 写入 result
    cols = ["effective_ts", "multiplier", "direction", "score", "leg_start_ts", "leg_end_ts",
            "leg_low", "leg_high", "levels_json", "invalidated_ts", "invalidate_reason",
            "source", "parent_eff_ts"]
    result_df = pd.DataFrame(all_records, columns=cols) if all_records else pd.DataFrame(columns=cols)
    writer.write_result(result_df)

    log.info(f'[fib_retracement] 管道完成: klines={n} result_rows={len(result_df)} invalidations={invalidation_count}')
    return {"klines": n, "result_rows": len(result_df), "invalidations": invalidation_count}
