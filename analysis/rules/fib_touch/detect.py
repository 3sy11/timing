"""fib_touch v2 — 纯定量proximity测量 + 因子计算 + 衍生score。

核心变化：
- 全部7线参与（含0%/100%），不再分type分类
- proximity为连续值[0,1]，不做阈值过滤
- 输出原始因子 + 一个衍生加权score字段(score_derived)
- Analysis只做测量，所有定性判断留给Decision层
"""
import logging
from typing import Dict, List, Tuple
import pandas as pd
from computation.algo.fib_retracement.models import FibGroup
from .config import FibTouchConfig

log = logging.getLogger(__name__)

# ratio重要性权重：0.618/0.5最关键，0%/100%最低
_RATIO_IMPORTANCE = {0.0: 0.3, 0.236: 0.5, 0.382: 0.7, 0.5: 0.9, 0.618: 1.0, 0.786: 0.7, 1.0: 0.3}


def _ratio_importance(ratio: float) -> float:
    best_dist, best_w = 1.0, 0.5
    for r, w in _RATIO_IMPORTANCE.items():
        if abs(ratio - r) < best_dist:
            best_dist, best_w = abs(ratio - r), w
    return best_w


def measure_proximity(close: float, groups: List[FibGroup], cfg: FibTouchConfig) -> List[dict]:
    """对所有7线测量proximity，返回感知半径内的记录。纯测量，无过滤逻辑。"""
    proximity_k = cfg.proximity_k
    min_leg_pct = cfg.min_leg_range_pct
    records = []
    for gi, g in enumerate(groups):
        leg_range = g.leg.high - g.leg.low
        if leg_range <= 0:
            continue
        if close > 0 and leg_range / close < min_leg_pct:
            continue
        max_dist = leg_range * proximity_k
        for ratio, lp in g.levels:
            dist = abs(close - lp)
            if dist > max_dist:
                continue
            prox = round(1.0 - dist / max_dist, 4)
            records.append({"group_idx": gi, "multiplier": g.multiplier, "direction": g.direction,
                           "ratio": ratio, "level_price": lp, "distance": round(dist, 4), "proximity": prox})
    return records


def compute_consensus(records: List[dict], tolerance: float) -> Dict[int, int]:
    """统计每条记录的level_price附近有多少组独立fib线共振。返回 {record_idx: consensus_count}。"""
    n = len(records)
    result = {}
    for i in range(n):
        lp = records[i]["level_price"]
        seen_keys = {(records[i]["multiplier"], records[i]["direction"])}
        count = 1
        for j in range(n):
            if i == j:
                continue
            key = (records[j]["multiplier"], records[j]["direction"])
            if key in seen_keys:
                continue
            if abs(records[j]["level_price"] - lp) <= tolerance:
                seen_keys.add(key)
                count += 1
        result[i] = count
    return result


def evaluate_level_history(df: pd.DataFrame, level_price: float, dynamic_tol: float, bar_idx: int, lookback_bars: int) -> dict:
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
    return {"touch_count": touches, "bounce_rate": round(bounces / touches, 4) if touches > 0 else 0.0}


def compute_volume_ratio(df: pd.DataFrame, bar_idx: int, lookback: int) -> float:
    if bar_idx < lookback or "volume" not in df.columns:
        return 1.0
    vol_loc = df.columns.get_loc("volume")
    total = sum(df.iat[j, vol_loc] for j in range(bar_idx - lookback, bar_idx))
    avg = total / lookback
    cur = df.iat[bar_idx, vol_loc]
    return round(cur / avg, 4) if avg > 0 else 1.0


def compute_approach(closes: List[float], level_price: float) -> str:
    if len(closes) < 2:
        return "unknown"
    prev, cur = closes[-2], closes[-1]
    if prev > level_price > cur:
        return "from_above"
    elif prev < level_price < cur:
        return "from_below"
    return "at_level"


def compute_score_derived(proximity: float, bounce_rate: float, volume_ratio: float, consensus: int, ratio: float, cfg: FibTouchConfig) -> float:
    """衍生加权得分。权重可配，结果为连续值。"""
    vol_cap = cfg.volume_cap
    score = (proximity * cfg.w_proximity
           + bounce_rate * cfg.w_bounce
           + min(volume_ratio, vol_cap) / vol_cap * cfg.w_volume
           + consensus * cfg.w_consensus
           + _ratio_importance(ratio) * cfg.w_ratio)
    return round(score, 4)


def detect_bar_signals(close: float, closes: List[float], df: pd.DataFrame, groups: List[FibGroup], bar_idx: int, cooldown_state: Dict[Tuple, int], cfg: FibTouchConfig) -> List[dict]:
    """对单根bar测量所有fib线的proximity并计算因子，产出定量信号。"""
    ts_loc = df.columns.get_loc("ts")
    bar_ts = int(df.iat[bar_idx, ts_loc])
    # 测量proximity
    prox_records = measure_proximity(close, groups, cfg)
    if not prox_records:
        return []
    # cooldown过滤（同一线短期内不重复产出）
    filtered = []
    for rec in prox_records:
        key = (rec["group_idx"], rec["ratio"])
        if bar_idx - cooldown_state.get(key, -999) < cfg.cooldown_bars:
            continue
        cooldown_state[key] = bar_idx
        filtered.append(rec)
    if not filtered:
        return []
    # consensus（该bar上多少组共振）
    avg_leg_range = sum(r.get("distance", 0) for r in filtered) / len(filtered) if filtered else 50.0
    consensus_tol = max(30.0, avg_leg_range * 2)
    consensus_map = compute_consensus(filtered, consensus_tol)
    # volume
    vol_ratio = compute_volume_ratio(df, bar_idx, cfg.volume_lookback)
    # approach
    approach = compute_approach(closes, filtered[0]["level_price"]) if filtered else "unknown"
    # 组装信号
    signals = []
    for idx, rec in enumerate(filtered):
        # bounce_rate / touch_count
        leg_range = 0.0
        for g in groups:
            if g.multiplier == rec["multiplier"] and g.direction == rec["direction"]:
                leg_range = g.leg.high - g.leg.low
                break
        history_tol = leg_range * cfg.proximity_k * 0.5
        history = evaluate_level_history(df, rec["level_price"], history_tol, bar_idx, cfg.history_lookback_bars)
        consensus = consensus_map.get(idx, 1)
        score = compute_score_derived(rec["proximity"], history["bounce_rate"], vol_ratio, consensus, rec["ratio"], cfg)
        signals.append({
            "ts": bar_ts, "close": close,
            "multiplier": rec["multiplier"], "direction": rec["direction"],
            "ratio": rec["ratio"], "level_price": rec["level_price"],
            # 原始因子
            "distance": rec["distance"], "proximity": rec["proximity"],
            "bounce_rate": history["bounce_rate"], "touch_count": history["touch_count"],
            "volume_ratio": vol_ratio, "consensus": consensus, "approach": approach,
            # 衍生字段
            "score_derived": score,
        })
    return signals


def run_detection(klines: List[dict], groups: List[FibGroup], cfg: FibTouchConfig = None, groups_resolver=None) -> dict:
    """批量扫描K线，产出定量proximity信号 + 摘要。"""
    cfg = cfg or FibTouchConfig()
    from computation.algo.fib_retracement.algo import base_df
    df = base_df(klines)
    n = len(df)
    if n == 0 or (not groups_resolver and not groups):
        return {"signals": [], "summary": {"total_signals": 0}}
    start = max(0, n - cfg.scan_bars) if cfg.scan_bars > 0 else 0
    closes_list = df["close"].tolist()
    all_signals = []
    cooldown_state: Dict[Tuple, int] = {}
    for i in range(start, n):
        close_i = closes_list[i]
        bar_ts = int(df.iat[i, df.columns.get_loc("ts")])
        cur_groups = groups_resolver(bar_ts) if groups_resolver else groups
        if not cur_groups:
            continue
        sigs = detect_bar_signals(close_i, closes_list[:i + 1], df, cur_groups, i, cooldown_state, cfg)
        all_signals.extend(sigs)
    high_score = sum(1 for s in all_signals if s["score_derived"] >= 3.0)
    return {"signals": all_signals,
            "summary": {"total_signals": len(all_signals), "high_score_count": high_score,
                        "avg_score": round(sum(s["score_derived"] for s in all_signals) / len(all_signals), 3) if all_signals else 0}}
