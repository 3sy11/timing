"""fib_retracement pipeline — 5步管道编排，每步写 Parquet 中间表。"""
import json, logging
from dataclasses import asdict
from typing import List
import pandas as pd
from .algo import (base_df, tag_pivots, tag_zigzag, tag_regression, compute_confidence,
                   cluster_prices, extract_trend_legs, score_and_rank,
                   adaptive_window_start, merge_legs_weighted, fit_fib_groups)
from .config import RetracementConfig
from ...writer import StepWriter

log = logging.getLogger(__name__)


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

    # ── step4: trend legs extraction + ranking ──
    n = len(feature_df)
    effective_end = max(0, n - cfg.skip_recent)
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

    # ── result: fib groups ──
    all_groups = []
    for mult in (1, 2, 3):
        mult_legs = [lg for lg in all_legs_records if lg["multiplier"] == mult]
        from .models import TrendLeg
        ranked_objs = [TrendLeg(**{k: v for k, v in r.items() if k != "multiplier"}) for r in mult_legs]
        up_legs = [lg for lg in ranked_objs if lg.direction == "up"]
        down_legs = [lg for lg in ranked_objs if lg.direction == "down"]
        merged = []
        if up_legs: merged.append(merge_legs_weighted(up_legs))
        if down_legs: merged.append(merge_legs_weighted(down_legs))
        groups = fit_fib_groups(merged, ratios=cfg.std_ratios)
        all_groups.extend(groups)
    result_records = []
    for g in all_groups:
        result_records.append({"direction": g.direction, "score": g.score,
                               "leg_start_ts": g.leg.start_ts, "leg_end_ts": g.leg.end_ts,
                               "leg_low": g.leg.low, "leg_high": g.leg.high,
                               "levels_json": json.dumps(g.levels)})
    result_df = pd.DataFrame(result_records) if result_records else pd.DataFrame(columns=["direction", "score", "leg_start_ts", "leg_end_ts", "leg_low", "leg_high", "levels_json"])
    writer.write_result(result_df)

    log.info(f'[fib_retracement] 管道完成: klines={len(klines)} legs={len(all_legs_records)} groups={len(all_groups)}')
    return {"klines": len(klines), "legs": len(all_legs_records), "groups": len(all_groups)}
