"""触线信号分析：多维特征评分 — 共识强度、接近方向、历史质量、K线形态、成交量。

六个特征维度独立计算后加权合并为信号分数：
  1. 多组共识强度 — 当前价格附近有多少组 Fib 线命中，方向是否一致
  2. 价格接近方向 — 从上方/下方接近 level，逆势接近加分
  3. 历史触线质量 — 该 level 过去 N 根 bar 内的触碰次数和反转概率
  4. K 线形态确认 — 锤子线/射击之星/十字星等
  5. 成交量确认   — 触线时成交量是否放大
  6. 触线间隔     — 距上次触同一条线的 bar 数，防抖用

所有函数不依赖 app/hub，可在 notebook / 测试中直接调用。
"""
import logging
from typing import Dict, List, Tuple
import pandas as pd
from . import config
from .models import FibGroup

log = logging.getLogger(__name__)

_KEY_RATIOS = {0.236, 0.382, 0.618, 0.786}


# ═══════════════════════════════════════════════════
#  特征一：多组共识强度
# ═══════════════════════════════════════════════════

def compute_consensus_strength(price: float, groups: List[FibGroup], tolerance: float) -> dict:
    """当前价格附近有多少组 Fib 线命中 → groups_hit × 方向奖励 × 关键比率奖励。"""
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


# ═══════════════════════════════════════════════════
#  特征二：价格接近方向
# ═══════════════════════════════════════════════════

def detect_approach_direction(closes: List[float], level_price: float, lookback: int = 5) -> dict:
    if len(closes) < 2:
        return {"approach": "unknown", "slope": 0.0, "momentum": 0.0, "counter_trend": False}
    recent = closes[-lookback:] if len(closes) >= lookback else closes
    curr, prev = closes[-1], closes[-2]
    if prev > level_price > curr: approach = "from_above"
    elif prev < level_price < curr: approach = "from_below"
    else: approach = "at_level"
    slope = (recent[-1] - recent[0]) / len(recent) if len(recent) >= 2 else 0.0
    momentum = abs(slope) / level_price if level_price > 0 else 0.0
    return {"approach": approach, "slope": slope, "momentum": momentum,
            "counter_trend": approach in ("from_above", "from_below")}


# ═══════════════════════════════════════════════════
#  特征三：历史触线质量
# ═══════════════════════════════════════════════════

def evaluate_level_history(df: pd.DataFrame, level_price: float, tolerance: float,
                           bar_idx: int, lookback_bars: int = 200) -> dict:
    """回溯 bar_idx 之前的 lookback_bars 根，统计反转率和平均反弹幅度。"""
    start = max(0, bar_idx - lookback_bars)
    lo_loc, hi_loc, cl_loc = df.columns.get_loc("low"), df.columns.get_loc("high"), df.columns.get_loc("close")
    touches, bounces, bounce_pcts = 0, 0, []
    for i in range(start, bar_idx):
        if not (df.iat[i, lo_loc] <= level_price + tolerance and df.iat[i, hi_loc] >= level_price - tolerance):
            continue
        touches += 1
        if i + 1 >= bar_idx: continue
        cc, nc = df.iat[i, cl_loc], df.iat[i + 1, cl_loc]
        if cc < level_price and nc > cc:
            bounces += 1; bounce_pcts.append((nc - cc) / cc)
        elif cc > level_price and nc < cc:
            bounces += 1; bounce_pcts.append((cc - nc) / cc)
    return {"touch_count": touches,
            "bounce_rate": bounces / touches if touches > 0 else 0.0,
            "avg_bounce_pct": sum(bounce_pcts) / len(bounce_pcts) if bounce_pcts else 0.0}


# ═══════════════════════════════════════════════════
#  特征四：K 线形态确认
# ═══════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════
#  特征五：成交量确认
# ═══════════════════════════════════════════════════

def volume_confirmation(df: pd.DataFrame, bar_idx: int, lookback: int = 20,
                        threshold: float = 1.5) -> dict:
    if bar_idx < lookback or "volume" not in df.columns:
        return {"volume_ratio": 1.0, "is_high_volume": False}
    vol_loc = df.columns.get_loc("volume")
    total = sum(df.iat[j, vol_loc] for j in range(bar_idx - lookback, bar_idx))
    avg = total / lookback
    cur = df.iat[bar_idx, vol_loc]
    ratio = cur / avg if avg > 0 else 1.0
    return {"volume_ratio": round(ratio, 2), "is_high_volume": ratio > threshold}


# ═══════════════════════════════════════════════════
#  单 bar 信号评分
# ═══════════════════════════════════════════════════

def score_bar_signals(close: float, bar: dict, closes: List[float],
                      df: pd.DataFrame, groups: List[FibGroup], bar_idx: int,
                      touch_history: Dict[Tuple, int]) -> List[dict]:
    """对单根 bar 的所有命中 level 做多维评分，返回 signal 列表。"""
    consensus = compute_consensus_strength(close, groups, config.TOUCH_TOLERANCE)
    if consensus["strength"] == 0: return []
    ts_loc = df.columns.get_loc("ts")
    signals = []
    for hit in consensus["hits"]:
        gi, ratio, lp = hit["group_idx"], hit["ratio"], hit["level_price"]
        key = (gi, ratio)
        if bar_idx - touch_history.get(key, -999) < config.TOUCH_COOLDOWN_BARS: continue
        approach = detect_approach_direction(closes, lp, config.TOUCH_APPROACH_LOOKBACK)
        history = evaluate_level_history(df, lp, config.TOUCH_TOLERANCE, bar_idx, config.TOUCH_HISTORY_LOOKBACK_BARS)
        candle = detect_candle_pattern(bar, lp)
        volume = volume_confirmation(df, bar_idx, config.TOUCH_VOLUME_LOOKBACK, config.TOUCH_VOLUME_THRESHOLD)
        score = (consensus["groups_hit"] * config.TOUCH_W_CONSENSUS +
                 history["bounce_rate"] * config.TOUCH_W_BOUNCE_RATE +
                 history["touch_count"] * config.TOUCH_W_TOUCH_COUNT +
                 (1.0 if volume["is_high_volume"] else 0) * config.TOUCH_W_VOLUME +
                 (1.0 if approach["counter_trend"] else 0) * config.TOUCH_W_COUNTER_TREND +
                 (0.5 if any(p in candle["patterns"] for p in ("hammer", "shooting_star")) else 0) * config.TOUCH_W_CANDLE)
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


# ═══════════════════════════════════════════════════
#  突破检测
# ═══════════════════════════════════════════════════

def check_breakout(close: float, groups: List[FibGroup], tolerance: float = 0.0) -> List[dict]:
    """价格突破趋势腿边界 → 该组 Fib 失效。"""
    broken = []
    for i, g in enumerate(groups):
        if close > g.leg.high + tolerance:
            broken.append({"group_idx": i, "direction": g.direction, "break_side": "above_high"})
        elif close < g.leg.low - tolerance:
            broken.append({"group_idx": i, "direction": g.direction, "break_side": "below_low"})
    return broken


# ═══════════════════════════════════════════════════
#  编排纯函数（notebook / 测试直接调用）
# ═══════════════════════════════════════════════════

def compute_touch_signals(klines: List[dict], groups: List[FibGroup]) -> dict:
    """扫描 K 线，对每个触线位做多维信号评分 + 突破检测。
    参数全部从 config 模块读取。
    输入：K 线 + FibGroups（来自 compute_retracement）。
    输出：signals 列表、breakouts 列表、汇总统计。
    """
    from .algo import base_df
    df = base_df(klines)
    n = len(df)
    if n == 0 or not groups:
        return {"signals": [], "breakouts": [], "summary": _empty_summary()}
    start = max(0, n - config.TOUCH_SCAN_BARS) if config.TOUCH_SCAN_BARS > 0 else 0
    closes_list = df["close"].tolist()
    all_signals, all_breakouts = [], []
    touch_history: Dict[Tuple, int] = {}
    ts_loc = df.columns.get_loc("ts")
    cols = ("open", "high", "low", "close", "volume", "ts")
    col_locs = {c: df.columns.get_loc(c) for c in cols}
    for i in range(start, n):
        close_i = closes_list[i]
        bar = {c: df.iat[i, col_locs[c]] for c in cols}
        sigs = score_bar_signals(close_i, bar, closes_list[:i + 1], df, groups, i, touch_history)
        all_signals.extend(sigs)
        broken = check_breakout(close_i, groups, config.TOUCH_BREAKOUT_TOLERANCE)
        for b in broken:
            b.update({"bar_idx": i, "ts": int(df.iat[i, ts_loc]), "close": close_i})
        all_breakouts.extend(broken)
    strong = sum(1 for s in all_signals if s["score"] >= config.TOUCH_STRONG_THRESHOLD)
    medium = sum(1 for s in all_signals if config.TOUCH_MEDIUM_THRESHOLD <= s["score"] < config.TOUCH_STRONG_THRESHOLD)
    weak = sum(1 for s in all_signals if config.TOUCH_WEAK_THRESHOLD <= s["score"] < config.TOUCH_MEDIUM_THRESHOLD)
    log.info(f'touch scan: bars={n - start} signals={len(all_signals)} strong={strong} medium={medium} weak={weak} breakouts={len(all_breakouts)}')
    return {"signals": all_signals, "breakouts": all_breakouts,
            "summary": {"total_touches": len(all_signals), "strong_signals": strong,
                        "medium_signals": medium, "weak_signals": weak,
                        "total_breakouts": len(all_breakouts)}}


def _empty_summary():
    return {"total_touches": 0, "strong_signals": 0, "medium_signals": 0, "weak_signals": 0, "total_breakouts": 0}
