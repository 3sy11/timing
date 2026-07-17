"""fib_touch 检测纯函数：多维特征评分 + 突破检测。

六个特征维度独立计算后加权合并为信号分数：
  1. 多组共识强度 — 当前价格附近有多少组 Fib 线命中
  2. 价格接近方向 — 从上方/下方接近 level，逆势接近加分
  3. 历史触线质量 — 该 level 过去 N 根 bar 内的触碰次数和反转概率
  4. K 线形态确认 — 锤子线/射击之星/十字星等
  5. 成交量确认   — 触线时成交量是否放大
  6. 触线间隔     — 距上次触同一条线的 bar 数，防抖用
"""
import logging
from typing import Dict, List, Tuple

import pandas as pd

from computation.algo.fib_retracement.models import FibGroup
from .config import FibTouchConfig

log = logging.getLogger(__name__)

_KEY_RATIOS = {0.236, 0.382, 0.618, 0.786}


def compute_consensus_strength(price: float, groups: List[FibGroup],
                               tolerance: float = None, cfg: FibTouchConfig = None) -> dict:
    cfg = cfg or FibTouchConfig()
    tolerance = tolerance if tolerance is not None else cfg.touch_tolerance
    hits = []
    for gi, g in enumerate(groups):
        for ratio, lp in g.levels:
            if abs(price - lp) <= tolerance:
                hits.append({"group_idx": gi, "direction": g.direction, "ratio": ratio,
                             "level_price": lp, "distance": abs(price - lp), "group_score": g.score})
    if not hits:
        return {"strength": 0, "groups_hit": 0, "directions_hit": [], "hits": []}
    groups_hit = len(set(h["group_idx"] for h in hits))
    dirs_hit = list(set(h["direction"] for h in hits))
    dir_bonus = 1.5 if len(dirs_hit) > 1 else 1.0
    ratio_bonus = sum(1.2 if h["ratio"] in _KEY_RATIOS else 1.0 for h in hits) / len(hits)
    return {"strength": round(groups_hit * dir_bonus * ratio_bonus, 3),
            "groups_hit": groups_hit, "directions_hit": dirs_hit, "hits": hits}


def detect_approach_direction(closes: List[float], level_price: float,
                              lookback: int = None, cfg: FibTouchConfig = None) -> dict:
    cfg = cfg or FibTouchConfig()
    lookback = lookback if lookback is not None else cfg.approach_lookback
    if len(closes) < 2:
        return {"approach": "unknown", "slope": 0.0, "momentum": 0.0, "counter_trend": False}
    recent = closes[-lookback:] if len(closes) >= lookback else closes
    curr, prev = closes[-1], closes[-2]
    if prev > level_price > curr:
        approach = "from_above"
    elif prev < level_price < curr:
        approach = "from_below"
    else:
        approach = "at_level"
    slope = (recent[-1] - recent[0]) / len(recent) if len(recent) >= 2 else 0.0
    momentum = abs(slope) / level_price if level_price > 0 else 0.0
    return {"approach": approach, "slope": slope, "momentum": momentum,
            "counter_trend": approach in ("from_above", "from_below")}


def evaluate_level_history(df: pd.DataFrame, level_price: float, tolerance: float,
                           bar_idx: int, lookback_bars: int = None,
                           cfg: FibTouchConfig = None) -> dict:
    cfg = cfg or FibTouchConfig()
    lookback_bars = lookback_bars if lookback_bars is not None else cfg.history_lookback_bars
    start = max(0, bar_idx - lookback_bars)
    lo_loc, hi_loc, cl_loc = df.columns.get_loc("low"), df.columns.get_loc("high"), df.columns.get_loc("close")
    touches, bounces, bounce_pcts = 0, 0, []
    for i in range(start, bar_idx):
        if not (df.iat[i, lo_loc] <= level_price + tolerance and df.iat[i, hi_loc] >= level_price - tolerance):
            continue
        touches += 1
        if i + 1 >= bar_idx:
            continue
        cc, nc = df.iat[i, cl_loc], df.iat[i + 1, cl_loc]
        if cc < level_price and nc > cc:
            bounces += 1
            bounce_pcts.append((nc - cc) / cc)
        elif cc > level_price and nc < cc:
            bounces += 1
            bounce_pcts.append((cc - nc) / cc)
    return {"touch_count": touches,
            "bounce_rate": bounces / touches if touches > 0 else 0.0,
            "avg_bounce_pct": sum(bounce_pcts) / len(bounce_pcts) if bounce_pcts else 0.0}


def detect_candle_pattern(bar: dict, level_price: float) -> dict:
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    body = abs(c - o)
    upper_wick, lower_wick = h - max(o, c), min(o, c) - l
    rng = h - l if h != l else 1e-10
    patterns = []
    if lower_wick > body * 2 and l <= level_price + rng * 0.1:
        patterns.append("hammer")
    if upper_wick > body * 2 and h >= level_price - rng * 0.1:
        patterns.append("shooting_star")
    if body < rng * 0.1:
        patterns.append("doji")
    if c > level_price and o < level_price:
        patterns.append("bullish_breakout")
    elif c < level_price and o > level_price:
        patterns.append("bearish_breakout")
    return {"patterns": patterns, "body_ratio": body / rng,
            "lower_wick_ratio": lower_wick / rng, "upper_wick_ratio": upper_wick / rng}


def volume_confirmation(df: pd.DataFrame, bar_idx: int, lookback: int = None,
                        threshold: float = None, cfg: FibTouchConfig = None) -> dict:
    cfg = cfg or FibTouchConfig()
    lookback = lookback if lookback is not None else cfg.volume_lookback
    threshold = threshold if threshold is not None else cfg.volume_threshold
    if bar_idx < lookback or "volume" not in df.columns:
        return {"volume_ratio": 1.0, "is_high_volume": False}
    vol_loc = df.columns.get_loc("volume")
    total = sum(df.iat[j, vol_loc] for j in range(bar_idx - lookback, bar_idx))
    avg = total / lookback
    cur = df.iat[bar_idx, vol_loc]
    ratio = cur / avg if avg > 0 else 1.0
    return {"volume_ratio": round(ratio, 2), "is_high_volume": ratio > threshold}


def score_bar_signals(close: float, bar: dict, closes: List[float],
                      df: pd.DataFrame, groups: List[FibGroup], bar_idx: int,
                      touch_history: Dict[Tuple, int],
                      cfg: FibTouchConfig = None) -> List[dict]:
    cfg = cfg or FibTouchConfig()
    consensus = compute_consensus_strength(close, groups, cfg=cfg)
    if consensus["strength"] == 0:
        return []
    ts_loc = df.columns.get_loc("ts")
    signals = []
    for hit in consensus["hits"]:
        gi, ratio, lp = hit["group_idx"], hit["ratio"], hit["level_price"]
        key = (gi, ratio)
        if bar_idx - touch_history.get(key, -999) < cfg.cooldown_bars:
            continue
        approach = detect_approach_direction(closes, lp, cfg=cfg)
        history = evaluate_level_history(df, lp, cfg.touch_tolerance, bar_idx, cfg=cfg)
        candle = detect_candle_pattern(bar, lp)
        volume = volume_confirmation(df, bar_idx, cfg=cfg)
        score = (consensus["groups_hit"] * cfg.w_consensus +
                 history["bounce_rate"] * cfg.w_bounce_rate +
                 history["touch_count"] * cfg.w_touch_count +
                 (1.0 if volume["is_high_volume"] else 0) * cfg.w_volume +
                 (1.0 if approach["counter_trend"] else 0) * cfg.w_counter_trend +
                 (0.5 if any(p in candle["patterns"] for p in ("hammer", "shooting_star")) else 0) * cfg.w_candle)
        touch_history[key] = bar_idx
        signals.append({
            "bar_idx": bar_idx, "ts": int(df.iat[bar_idx, ts_loc]), "close": close,
            "level_price": lp, "ratio": ratio, "group_idx": gi,
            "direction": hit["direction"], "score": round(score, 3),
            "consensus": consensus["groups_hit"], "directions_hit": consensus["directions_hit"],
            "bounce_rate": history["bounce_rate"], "touch_count": history["touch_count"],
            "high_volume": volume["is_high_volume"], "volume_ratio": volume["volume_ratio"],
            "patterns": candle["patterns"], "approach": approach["approach"],
            "counter_trend": approach["counter_trend"],
        })
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


def check_breakout(close: float, groups: List[FibGroup],
                   tolerance: float = None, cfg: FibTouchConfig = None) -> List[dict]:
    cfg = cfg or FibTouchConfig()
    tolerance = tolerance if tolerance is not None else cfg.breakout_tolerance
    broken = []
    for i, g in enumerate(groups):
        if close > g.leg.high + tolerance:
            broken.append({"group_idx": i, "direction": g.direction, "break_side": "above_high"})
        elif close < g.leg.low - tolerance:
            broken.append({"group_idx": i, "direction": g.direction, "break_side": "below_low"})
    return broken


def run_detection(klines: List[dict], groups: List[FibGroup],
                  cfg: FibTouchConfig = None,
                  groups_resolver=None) -> dict:
    """批量扫描 K 线，产出信号 + 突破 + 摘要。Rule 的检测入口函数。
    groups_resolver: 可选 callable(bar_ts) -> List[FibGroup]，提供时间感知的动态 groups。
    若提供则忽略静态 groups 参数。
    """
    cfg = cfg or FibTouchConfig()
    from computation.algo.fib_retracement.algo import base_df
    df = base_df(klines)
    n = len(df)
    if n == 0:
        return {"signals": [], "breakouts": [], "summary": _empty_summary()}
    if not groups_resolver and not groups:
        return {"signals": [], "breakouts": [], "summary": _empty_summary()}
    start = max(0, n - cfg.scan_bars) if cfg.scan_bars > 0 else 0
    closes_list = df["close"].tolist()
    all_signals, all_breakouts = [], []
    touch_history: Dict[Tuple, int] = {}
    ts_loc = df.columns.get_loc("ts")
    cols = ("open", "high", "low", "close", "volume", "ts")
    col_locs = {c: df.columns.get_loc(c) for c in cols}
    for i in range(start, n):
        close_i = closes_list[i]
        bar_ts = int(df.iat[i, ts_loc])
        cur_groups = groups_resolver(bar_ts) if groups_resolver else groups
        if not cur_groups:
            continue
        bar = {c: df.iat[i, col_locs[c]] for c in cols}
        sigs = score_bar_signals(close_i, bar, closes_list[:i + 1], df, cur_groups, i, touch_history, cfg=cfg)
        all_signals.extend(sigs)
        broken = check_breakout(close_i, cur_groups, cfg=cfg)
        for b in broken:
            b.update({"bar_idx": i, "ts": bar_ts, "close": close_i})
        all_breakouts.extend(broken)
    strong = sum(1 for s in all_signals if s["score"] >= cfg.strong_threshold)
    medium = sum(1 for s in all_signals if cfg.medium_threshold <= s["score"] < cfg.strong_threshold)
    weak = sum(1 for s in all_signals if cfg.weak_threshold <= s["score"] < cfg.medium_threshold)
    return {"signals": all_signals, "breakouts": all_breakouts,
            "summary": {"total_signals": len(all_signals), "strong_signals": strong,
                        "medium_signals": medium, "weak_signals": weak,
                        "total_breakouts": len(all_breakouts)}}


def _empty_summary():
    return {"total_signals": 0, "strong_signals": 0, "medium_signals": 0,
            "weak_signals": 0, "total_breakouts": 0}
