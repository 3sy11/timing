"""fib_touch 检测纯函数：动态容差 + 结构化特征输出 + 突破信号。

方案E: tolerance = leg_range × tolerance_k，proximity = 1 - distance/tolerance。
过滤: leg_range / close < min_leg_range_pct 的窄 group 不参与检测。
突破: close 突破 leg 边界时产出 type=breakout 信号。
"""
import logging
from typing import Dict, List, Tuple
import pandas as pd
from computation.algo.fib_retracement.models import FibGroup
from .config import FibTouchConfig

log = logging.getLogger(__name__)


def find_hits(price: float, groups: List[FibGroup], cfg: FibTouchConfig) -> List[dict]:
    """对每个 group 用动态容差判定命中，过滤窄 leg，返回 hit 列表。"""
    tolerance_k = cfg.tolerance_k
    min_leg_pct = cfg.min_leg_range_pct
    hits = []
    for gi, g in enumerate(groups):
        leg_range = g.leg.high - g.leg.low
        if leg_range <= 0:
            continue
        if price > 0 and leg_range / price < min_leg_pct:
            continue
        dynamic_tol = leg_range * tolerance_k
        for ratio, lp in g.levels:
            dist = abs(price - lp)
            if dist > dynamic_tol:
                continue
            proximity = round(1.0 - dist / dynamic_tol, 4)
            hits.append({"group_idx": gi, "multiplier": g.multiplier,
                         "direction": g.direction, "ratio": ratio,
                         "level_price": lp, "distance": dist, "proximity": proximity,
                         "group_score": g.score, "leg_range": leg_range})
    return hits


def find_breakouts(close: float, groups: List[FibGroup], cfg: FibTouchConfig) -> List[dict]:
    """检测价格突破 leg 边界的事件，过滤窄 leg。"""
    btk = cfg.breakout_tolerance_k
    min_leg_pct = cfg.min_leg_range_pct
    broken = []
    for i, g in enumerate(groups):
        leg_range = g.leg.high - g.leg.low
        if leg_range <= 0:
            continue
        if close > 0 and leg_range / close < min_leg_pct:
            continue
        tol = leg_range * btk
        if close > g.leg.high + tol:
            broken.append({"group_idx": i, "multiplier": g.multiplier, "direction": g.direction,
                           "break_side": "above_high", "level_price": g.leg.high,
                           "leg_range": leg_range, "group_score": g.score})
        elif close < g.leg.low - tol:
            broken.append({"group_idx": i, "multiplier": g.multiplier, "direction": g.direction,
                           "break_side": "below_low", "level_price": g.leg.low,
                           "leg_range": leg_range, "group_score": g.score})
    return broken


def evaluate_level_history(df: pd.DataFrame, level_price: float, dynamic_tol: float,
                           bar_idx: int, lookback_bars: int) -> dict:
    start = max(0, bar_idx - lookback_bars)
    lo_loc, hi_loc, cl_loc = df.columns.get_loc("low"), df.columns.get_loc("high"), df.columns.get_loc("close")
    touches, bounces = 0, 0
    for i in range(start, bar_idx):
        if not (df.iat[i, lo_loc] <= level_price + dynamic_tol and df.iat[i, hi_loc] >= level_price - dynamic_tol):
            continue
        touches += 1
        if i + 1 >= bar_idx:
            continue
        cc, nc = df.iat[i, cl_loc], df.iat[i + 1, cl_loc]
        if (cc < level_price and nc > cc) or (cc > level_price and nc < cc):
            bounces += 1
    return {"touch_count": touches, "bounce_rate": bounces / touches if touches > 0 else 0.0}


def volume_confirmation(df: pd.DataFrame, bar_idx: int, cfg: FibTouchConfig) -> dict:
    lookback = cfg.volume_lookback
    if bar_idx < lookback or "volume" not in df.columns:
        return {"volume_ratio": 1.0, "high_volume": False}
    vol_loc = df.columns.get_loc("volume")
    total = sum(df.iat[j, vol_loc] for j in range(bar_idx - lookback, bar_idx))
    avg = total / lookback
    cur = df.iat[bar_idx, vol_loc]
    ratio = cur / avg if avg > 0 else 1.0
    return {"volume_ratio": round(ratio, 2), "high_volume": ratio > cfg.volume_threshold}


def detect_bar_signals(close: float, bar: dict, closes: List[float],
                       df: pd.DataFrame, groups: List[FibGroup], bar_idx: int,
                       touch_history: Dict[Tuple, int], cfg: FibTouchConfig) -> List[dict]:
    """对单根 bar 检测触碰 + 突破，统一产出信号。"""
    ts_loc = df.columns.get_loc("ts")
    bar_ts = int(df.iat[bar_idx, ts_loc])
    signals = []

    # ── 触碰信号 ──
    hits = find_hits(close, groups, cfg)
    if hits:
        vol = volume_confirmation(df, bar_idx, cfg)
        approach = "unknown"
        if len(closes) >= 2:
            prev = closes[-2]
            best_lp = hits[0]["level_price"]
            if prev > best_lp > close:
                approach = "from_above"
            elif prev < best_lp < close:
                approach = "from_below"
            else:
                approach = "at_level"
        for hit in hits:
            gi, ratio = hit["group_idx"], hit["ratio"]
            key = (gi, ratio)
            if bar_idx - touch_history.get(key, -999) < cfg.cooldown_bars:
                continue
            history = evaluate_level_history(df, hit["level_price"], hit["leg_range"] * cfg.tolerance_k,
                                             bar_idx, cfg.history_lookback_bars)
            touch_history[key] = bar_idx
            signals.append({
                "type": "touch", "bar_idx": bar_idx, "ts": bar_ts, "close": close,
                "level_price": hit["level_price"], "ratio": hit["ratio"],
                "multiplier": hit["multiplier"], "group_idx": gi, "direction": hit["direction"],
                "proximity": hit["proximity"], "distance": round(hit["distance"], 4),
                "leg_range": round(hit["leg_range"], 2), "group_score": round(hit["group_score"], 4),
                "bounce_rate": round(history["bounce_rate"], 4), "touch_count": history["touch_count"],
                "high_volume": vol["high_volume"], "volume_ratio": vol["volume_ratio"],
                "approach": approach,
            })

    # ── 突破信号: level_price 用 close 以便 Grafana 打点在 kline 上 ──
    breakouts = find_breakouts(close, groups, cfg)
    for b in breakouts:
        key = ("brk", b["group_idx"], b["break_side"])
        if bar_idx - touch_history.get(key, -999) < cfg.cooldown_bars:
            continue
        touch_history[key] = bar_idx
        signals.append({
            "type": "breakout", "bar_idx": bar_idx, "ts": bar_ts, "close": close,
            "level_price": close, "ratio": 0.0 if b["break_side"] == "above_high" else 1.0,
            "multiplier": b["multiplier"], "group_idx": b["group_idx"], "direction": b["direction"],
            "proximity": 1.0, "distance": abs(close - b["level_price"]),
            "leg_range": round(b["leg_range"], 2), "group_score": round(b["group_score"], 4),
            "bounce_rate": 0.0, "touch_count": 0,
            "high_volume": False, "volume_ratio": 1.0,
            "approach": b["break_side"],
        })

    signals.sort(key=lambda x: x["proximity"], reverse=True)
    return signals


def run_detection(klines: List[dict], groups: List[FibGroup],
                  cfg: FibTouchConfig = None, groups_resolver=None) -> dict:
    """批量扫描 K 线，产出信号（含触碰+突破）+ 摘要。"""
    cfg = cfg or FibTouchConfig()
    from computation.algo.fib_retracement.algo import base_df
    df = base_df(klines)
    n = len(df)
    if n == 0 or (not groups_resolver and not groups):
        return {"signals": [], "summary": _empty_summary()}
    start = max(0, n - cfg.scan_bars) if cfg.scan_bars > 0 else 0
    closes_list = df["close"].tolist()
    all_signals = []
    touch_history: Dict[Tuple, int] = {}
    cols = ("open", "high", "low", "close", "volume", "ts")
    col_locs = {c: df.columns.get_loc(c) for c in cols}
    for i in range(start, n):
        close_i = closes_list[i]
        bar_ts = int(df.iat[i, col_locs["ts"]])
        cur_groups = groups_resolver(bar_ts) if groups_resolver else groups
        if not cur_groups:
            continue
        bar = {c: df.iat[i, col_locs[c]] for c in cols}
        sigs = detect_bar_signals(close_i, bar, closes_list[:i + 1], df, cur_groups, i, touch_history, cfg)
        all_signals.extend(sigs)
    touches = [s for s in all_signals if s["type"] == "touch"]
    breakouts = [s for s in all_signals if s["type"] == "breakout"]
    high_prox = sum(1 for s in touches if s["proximity"] >= 0.9)
    med_prox = sum(1 for s in touches if 0.7 <= s["proximity"] < 0.9)
    return {"signals": all_signals,
            "summary": {"total_signals": len(all_signals), "touches": len(touches),
                        "breakouts": len(breakouts), "high_proximity": high_prox,
                        "medium_proximity": med_prox}}


def _empty_summary():
    return {"total_signals": 0, "touches": 0, "breakouts": 0, "high_proximity": 0, "medium_proximity": 0}
