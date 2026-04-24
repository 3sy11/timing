"""回测后处理：信号前瞻评估 + 汇总指标。"""
import logging
from typing import List

log = logging.getLogger(__name__)


def evaluate_outcome(klines: list, signal: dict, lookahead: int = 20, bounce_pct: float = 0.005) -> dict:
    """前瞻 lookahead 根 bar，判定信号是否有效反弹。"""
    idx = signal.get("bar_idx", 0)
    direction = signal.get("direction", "up")
    entry = signal.get("touch_price", 0)
    if not entry or idx + 1 >= len(klines):
        return {"bounced": False, "max_favorable": 0.0, "max_adverse": 0.0}
    end = min(idx + 1 + lookahead, len(klines))
    max_fav, max_adv = 0.0, 0.0
    for i in range(idx + 1, end):
        c = float(klines[i].get("close", 0) if isinstance(klines[i], dict) else klines[i])
        diff_pct = (c - entry) / entry if entry else 0
        if direction == "up":
            max_fav = max(max_fav, diff_pct)
            max_adv = min(max_adv, diff_pct)
        else:
            max_fav = max(max_fav, -diff_pct)
            max_adv = min(max_adv, -diff_pct)
    return {"bounced": max_fav >= bounce_pct, "max_favorable": round(max_fav, 6), "max_adverse": round(max_adv, 6)}


def compute_metrics(signals: List[dict]) -> dict:
    """汇总所有信号的命中指标。"""
    total = len(signals)
    if total == 0:
        return {"total": 0, "bounced": 0, "hit_rate": 0.0, "avg_favorable": 0.0, "avg_adverse": 0.0}
    bounced = sum(1 for s in signals if s.get("outcome", {}).get("bounced", False))
    avg_fav = sum(s.get("outcome", {}).get("max_favorable", 0) for s in signals) / total
    avg_adv = sum(s.get("outcome", {}).get("max_adverse", 0) for s in signals) / total
    return {"total": total, "bounced": bounced, "hit_rate": round(bounced / total, 4),
            "avg_favorable": round(avg_fav, 6), "avg_adverse": round(avg_adv, 6)}


def build_report(klines: list, signals: list, breakouts: list,
                 warmup_bars: int, bt_dir: str,
                 lookahead: int = 20, bounce_pct: float = 0.005) -> dict:
    """组装最终回测报告。"""
    for sig in signals:
        sig["outcome"] = evaluate_outcome(klines, sig, lookahead, bounce_pct)
    metrics = compute_metrics(signals)
    log.info(f'[Backtest] report: signals={metrics["total"]} hit_rate={metrics["hit_rate"]} breakouts={len(breakouts)}')
    return {"signals": signals, "breakouts": breakouts, "metrics": metrics,
            "klines_total": len(klines), "test_bars": len(klines) - warmup_bars,
            "warmup_bars": warmup_bars, "data_dir": bt_dir}
