"""price_touch detect — v5 基于全局 ratio_lines 的信号检测。

逻辑:
1. 从 ratio_lines (全局Fib管线产出) 获取所有 ratio 线
2. 逐 bar: close 是否在某条 ratio 线的 proximity 范围内
3. Zone 锁定去重
4. strength = f(consensus, proximity)
"""
import logging
from typing import Callable, Dict, List, Optional
import pandas as pd
from .config import PriceTouchConfig

log = logging.getLogger(__name__)


def _calc_direction(close: float, line_price: float) -> str:
    return "long" if close <= line_price else "short"


def detect_bar(close: float, bar_ts: int, ratio_lines: List[dict],
               lock_state: Dict[str, Optional[float]],
               cfg: PriceTouchConfig, max_consensus: int) -> List[dict]:
    """对单根 bar 检测是否触碰 ratio 线。"""
    candidates = []
    for rl in ratio_lines:
        price = rl["price"]
        radius = price * cfg.proximity_k * 0.01
        if radius <= 0: continue
        distance = abs(close - price)
        if distance > radius: continue
        proximity = round(1.0 - distance / radius, 4)
        norm_consensus = min(rl["consensus"] / max_consensus, 1.0) if max_consensus > 0 else 0.0
        strength = round(cfg.w_consensus * norm_consensus + cfg.w_proximity * proximity, 4)
        strength = min(max(strength, 0.0), 1.0)
        direction = _calc_direction(close, price)
        candidates.append({"line_price": round(price, 2), "ts": bar_ts,
                          "direction": direction, "strength": strength,
                          "price": close, "source_price": round(price, 2),
                          "ratio": rl["ratio"], "fib_low": rl["fib_low"], "fib_high": rl["fib_high"],
                          "consensus": rl["consensus"], "proximity": proximity})
    if not candidates: return []
    locked = lock_state.get("locked")
    new_cands = [c for c in candidates if c["line_price"] != locked]
    if not new_cands: return []
    best = max(new_cands, key=lambda c: c["strength"])
    lock_state["locked"] = best["line_price"]
    out = {k: v for k, v in best.items() if k != "line_price"}
    return [out]


def run_detection(klines_df: pd.DataFrame, ratio_lines: List[dict],
                  cfg: PriceTouchConfig = None, compute_id: str = "") -> dict:
    """批量扫描 klines，基于 ratio_lines 产出信号。"""
    cfg = cfg or PriceTouchConfig()
    n = len(klines_df)
    if n == 0 or not ratio_lines: return {"signals": [], "summary": {"total_signals": 0}}
    start = max(0, n - cfg.scan_bars) if cfg.scan_bars > 0 else 0
    closes = klines_df["close"].tolist()
    ts_list = klines_df["ts"].tolist()
    max_consensus = max(rl["consensus"] for rl in ratio_lines) if ratio_lines else 1
    all_signals: List[dict] = []
    lock_state: Dict[str, Optional[float]] = {"locked": None}
    for i in range(start, n):
        bar_ts = int(ts_list[i])
        sigs = detect_bar(closes[i], bar_ts, ratio_lines, lock_state, cfg, max_consensus)
        for s in sigs:
            s["compute_id"] = compute_id
        all_signals.extend(sigs)
    log.info(f'[price_touch] 检测完成: {len(all_signals)} 信号 (扫描 {n - start} bars)')
    return {"signals": all_signals,
            "summary": {"total_signals": len(all_signals),
                        "avg_strength": round(sum(s["strength"] for s in all_signals) / len(all_signals), 4) if all_signals else 0}}
