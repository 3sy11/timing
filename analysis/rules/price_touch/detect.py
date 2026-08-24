"""price_touch detect — v3 规则链判断 + 信号强度计算。

逻辑:
1. 获取当前 bar_ts 有效的 PriceLine + FibLevel 结构
2. 逐 PriceLine 检测 proximity
3. 规则链: proximity门槛 → 强度门槛 → zone锁定(新zone解锁旧zone) → (可选)Fib质量
4. 通过 → 计算 strength → 产出信号
"""
import logging
from typing import Callable, Dict, List, Optional
import pandas as pd
from .config import PriceTouchConfig

log = logging.getLogger(__name__)


def _calc_direction(has_high: bool, has_low: bool, close: float, center: float) -> str:
    if has_low and not has_high:
        return "long"
    if has_high and not has_low:
        return "short"
    return "long" if close <= center else "short"


def _normalize_line_strength(strength: float, max_strength: float) -> float:
    if max_strength <= 0:
        return 0.0
    return min(strength / max_strength, 1.0)


def detect_bar(close: float, bar_ts: int, snapshot: dict,
               lock_state: Dict[str, Optional[float]],
               cfg: PriceTouchConfig) -> List[dict]:
    """对单根 bar 检测所有 multiplier 的 PriceLine 触碰。
    lock_state: {"locked": center|None} — 全局共享锁定状态，
    同一 zone 不重复产出信号，直到新 zone 触发后解锁。所有 multiplier 共享锁。
    """
    candidates = []
    for mult, snap in snapshot.items():
        price_lines = snap["price_lines"]
        fib_levels = snap.get("fib_levels", [])
        fib_quality = snap.get("fib_quality", 0.0)
        if not price_lines:
            continue
        max_str = max(pl["line_strength"] for pl in price_lines) if price_lines else 1.0
        anchor_map = {}
        for fl in fib_levels:
            if fl.get("is_anchored") and fl.get("anchor_center") is not None:
                anchor_map[fl["anchor_center"]] = fl
        for pl in price_lines:
            center = pl["center"]
            radius = center * cfg.proximity_k * 0.01
            if radius <= 0:
                continue
            distance = abs(close - center)
            if distance > radius:
                continue
            proximity = round(1.0 - distance / radius, 4)
            line_strength = pl["line_strength"]
            if line_strength < cfg.min_strength:
                continue
            is_fib_backed = center in anchor_map
            if cfg.min_fib_quality > 0 and is_fib_backed and fib_quality < cfg.min_fib_quality:
                continue
            norm_str = _normalize_line_strength(line_strength, max_str)
            base_w = cfg.w_proximity + cfg.w_line + cfg.w_bidir
            base_score = (proximity * cfg.w_proximity + norm_str * cfg.w_line
                         + (cfg.w_bidir if pl.get("is_bidirectional") else 0.0)) / base_w if base_w > 0 else 0.0
            if is_fib_backed:
                strength = round(fib_quality * 0.5 + base_score * 0.5, 4)
            else:
                strength = round(base_score, 4)
            strength = min(max(strength, 0.0), 1.0)
            direction = _calc_direction(pl.get("has_high", False), pl.get("has_low", False), close, center)
            fib_ratio = anchor_map[center]["ratio"] if is_fib_backed else None
            candidates.append({"center": round(center, 2), "ts": bar_ts, "close": close,
                              "direction": direction, "strength": strength, "price": close,
                              "level": center, "multiplier": mult, "fib_ratio": fib_ratio})
    if not candidates:
        return []
    locked_center = lock_state.get("locked")
    new_candidates = [c for c in candidates if c["center"] != locked_center]
    if not new_candidates:
        return []
    best = max(new_candidates, key=lambda c: c["strength"])
    lock_state["locked"] = best["center"]
    return [{k: v for k, v in best.items() if k != "center"}]


def run_detection(klines_df: pd.DataFrame, resolver: Callable, cfg: PriceTouchConfig = None,
                  compute_id: str = "") -> dict:
    """批量扫描 klines，产出信号列表。
    resolver(bar_ts) → {mult: {price_lines, fib_levels, fib_quality}}
    """
    cfg = cfg or PriceTouchConfig()
    n = len(klines_df)
    if n == 0:
        return {"signals": [], "summary": {"total_signals": 0}}
    start = max(0, n - cfg.scan_bars) if cfg.scan_bars > 0 else 0
    closes = klines_df["close"].tolist()
    ts_list = klines_df["ts"].tolist()
    all_signals: List[dict] = []
    lock_state: Dict[str, Optional[float]] = {"locked": None}
    for i in range(start, n):
        bar_ts = int(ts_list[i])
        snapshot = resolver(bar_ts)
        if not snapshot:
            continue
        sigs = detect_bar(closes[i], bar_ts, snapshot, lock_state, cfg)
        for s in sigs:
            s["compute_id"] = compute_id
        all_signals.extend(sigs)
    log.info(f'[price_touch] 检测完成: {len(all_signals)} 信号 (扫描 {n - start} bars)')
    return {"signals": all_signals,
            "summary": {"total_signals": len(all_signals),
                        "avg_strength": round(sum(s["strength"] for s in all_signals) / len(all_signals), 4) if all_signals else 0}}
