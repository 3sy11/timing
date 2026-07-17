"""fib_retracement pipeline — 5步管道编排，每步写 Parquet 中间表。
scan_bars > 0 时启用滑动窗口：从 min_bars 开始每隔 scan_bars 重算 fib，输出时间序列。
"""
import json, logging
from dataclasses import asdict
from typing import List
import pandas as pd
from .algo import (base_df, tag_pivots, tag_zigzag, tag_regression, compute_confidence,
                   cluster_prices, extract_trend_legs, score_and_rank,
                   adaptive_window_start, merge_legs_weighted, fit_fib_groups)
from .config import RetracementConfig
from .models import TrendLeg
from ...writer import StepWriter

log = logging.getLogger(__name__)


def _compute_fib_at(feature_df, clusters_high_df, clusters_low_df, end_idx: int, cfg) -> list:
    """在 feature_df[:end_idx] 上计算 3 组 × 2 方向的 fib groups，返回 record list。"""
    effective_df = feature_df.iloc[:end_idx]
    effective_ts = int(effective_df.iloc[-1]["ts"])
    records = []
    for mult in (1, 2, 3):
        target_bars = cfg.recent_bars * mult
        actual_start = adaptive_window_start(effective_df, target_bars, min_conf=cfg.min_cluster_conf)
        recent_df = effective_df.iloc[actual_start:].reset_index(drop=True)
        if len(recent_df) < 10:
            continue
        legs = extract_trend_legs(recent_df, clusters_high_df, clusters_low_df, min_span_pct=cfg.min_leg_span_pct)
        ranked = score_and_rank(legs, top_n=cfg.top_n, total_bars=len(recent_df))
        if not ranked:
            continue
        ranked_objs = [TrendLeg(**{k: v for k, v in asdict(lg).items()}) for lg in ranked]
        up_legs = [lg for lg in ranked_objs if lg.direction == "up"]
        down_legs = [lg for lg in ranked_objs if lg.direction == "down"]
        merged = []
        if up_legs: merged.append(merge_legs_weighted(up_legs))
        if down_legs: merged.append(merge_legs_weighted(down_legs))
        groups = fit_fib_groups(merged, ratios=cfg.std_ratios)
        for g in groups:
            records.append({"effective_ts": effective_ts, "multiplier": mult,
                           "direction": g.direction, "score": g.score,
                           "leg_start_ts": g.leg.start_ts, "leg_end_ts": g.leg.end_ts,
                           "leg_low": g.leg.low, "leg_high": g.leg.high,
                           "levels_json": json.dumps(g.levels)})
    return records


def run_pipeline(klines: List[dict], cfg: RetracementConfig, writer: StepWriter) -> dict:
    """执行 fib_retracement 全管道，每步持久化中间表。返回摘要。"""
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

    # ── step4 + result: 滑动窗口 or 单次计算 ──
    n = len(feature_df)
    scan_bars = cfg.scan_bars
    min_bars = cfg.min_bars
    skip_recent = cfg.skip_recent

    all_result_records = []
    if scan_bars > 0:
        # 滑动窗口：从 min_bars 到 n-skip_recent，每隔 scan_bars 计算一次
        start_pos = max(min_bars, cfg.recent_bars * 3)
        end_pos = max(0, n - skip_recent)
        scan_points = list(range(start_pos, end_pos + 1, scan_bars))
        if end_pos not in scan_points:
            scan_points.append(end_pos)
        log.info(f'[fib_retracement] 滑动窗口: scan_bars={scan_bars} 共 {len(scan_points)} 个计算点')
        for idx in scan_points:
            records = _compute_fib_at(feature_df, clusters_high_df, clusters_low_df, idx, cfg)
            all_result_records.extend(records)
    else:
        # 兼容旧模式：只计算最后一个时间点
        effective_end = max(0, n - skip_recent)
        records = _compute_fib_at(feature_df, clusters_high_df, clusters_low_df, effective_end, cfg)
        all_result_records.extend(records)

    # 写入 step4_legs (保持兼容，只存最后一个时间点的 legs)
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

    # 写入 result（时间序列格式）
    cols = ["effective_ts", "multiplier", "direction", "score", "leg_start_ts", "leg_end_ts", "leg_low", "leg_high", "levels_json"]
    result_df = pd.DataFrame(all_result_records, columns=cols) if all_result_records else pd.DataFrame(columns=cols)
    writer.write_result(result_df)

    log.info(f'[fib_retracement] 管道完成: klines={len(klines)} scan_points={len(scan_points) if scan_bars > 0 else 1} result_rows={len(result_df)}')
    return {"klines": len(klines), "result_rows": len(result_df), "scan_points": len(scan_points) if scan_bars > 0 else 1}
