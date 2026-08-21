"""price_touch detect — v3 规则链判断 + 信号强度计算。

逻辑:
1. 获取当前 bar_ts 有效的 PriceLine + FibLevel 结构
2. 逐 PriceLine 检测 proximity
3. 规则链: proximity门槛 → 强度门槛 → 冷却期 → (可选)Fib质量
4. 通过 → 计算 strength → 产出信号
"""
import logging
from typing import Callable, Dict, List, Tuple
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


def detect_bar(close: float, bar_ts: int, snapshot: dict, cooldown_state: Dict[Tuple, int],
               bar_idx: int, cfg: PriceTouchConfig) -> List[dict]:
    """对单根 bar 检测所有 multiplier 的 PriceLine 触碰。"""
    signals = []
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
            cool_key = (mult, round(center, 2))
            last_fire = cooldown_state.get(cool_key, -999)
            if bar_idx - last_fire < cfg.cooldown_bars:
                continue
            is_fib_backed = center in anchor_map
            cur_fib_quality = fib_quality if is_fib_backed else 0.0
            if cfg.min_fib_quality > 0 and is_fib_backed and fib_quality < cfg.min_fib_quality:
                continue
            norm_str = _normalize_line_strength(line_strength, max_str)
            strength = round(
                proximity * cfg.w_proximity
                + norm_str * cfg.w_line
                + cur_fib_quality * cfg.w_fib
                + (cfg.w_bidir if pl.get("is_bidirectional") else 0.0), 4)
            strength = min(max(strength, 0.0), 1.0)
            direction = _calc_direction(pl.get("has_high", False), pl.get("has_low", False), close, center)
            cooldown_state[cool_key] = bar_idx
            fib_ratio = anchor_map[center]["ratio"] if is_fib_backed else None
            signals.append({"ts": bar_ts, "close": close, "direction": direction,
                           "strength": strength, "price": close, "level": center,
                           "multiplier": mult, "fib_ratio": fib_ratio})
    return signals


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
    cooldown_state: Dict[Tuple, int] = {}
    for i in range(start, n):
        bar_ts = int(ts_list[i])
        snapshot = resolver(bar_ts)
        if not snapshot:
            continue
        sigs = detect_bar(closes[i], bar_ts, snapshot, cooldown_state, i, cfg)
        for s in sigs:
            s["compute_id"] = compute_id
        all_signals.extend(sigs)
    log.info(f'[price_touch] 检测完成: {len(all_signals)} 信号 (扫描 {n - start} bars)')
    return {"signals": all_signals,
            "summary": {"total_signals": len(all_signals),
                        "avg_strength": round(sum(s["strength"] for s in all_signals) / len(all_signals), 4) if all_signals else 0}}
