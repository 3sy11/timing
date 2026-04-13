"""Fib 拟合：retracement 计算 + fit_fib 搜索最优 H/L + ComputeFibRetracement 命令。
纯函数独立可调，Command 编排 + CSV 落盘。
Jupyter: levels = retracement_from_leg(100, 200); result = fit_fib(ch, cl, config)
"""
from typing import Any, ClassVar, List, Optional, Tuple
from bollydog.globals import app, hub, protocol
from bollydog.models.base import BaseCommand
from timing.analysis.config import FibConfig, DEFAULT_RATIOS
from timing.analysis.types import FibResult, PriceCluster
from timing.analysis.algo import dump_csv

# ── 纯函数：retracement ──
def compute_retracement_levels(high: float, low: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    span = high - low
    return [(r, low + span * r) for r in ratios]

def retracement_from_leg(leg_low: float, leg_high: float, ratios: Tuple[float, ...] = DEFAULT_RATIOS) -> List[Tuple[float, float]]:
    return compute_retracement_levels(leg_high, leg_low, ratios)

# ── 纯函数：fit_fib ──
def fit_fib(clusters_high: List[PriceCluster], clusters_low: List[PriceCluster],
            config: FibConfig) -> Optional[FibResult]:
    all_clusters = clusters_high + clusters_low
    if len(all_clusters) < 2: return None
    prices = sorted(set(c.center for c in all_clusters))
    if len(prices) < 2: return None
    price_range = prices[-1] - prices[0]
    min_span = price_range * config.min_span_pct
    best_score, best_h, best_l = 0.0, 0.0, 0.0
    for i in range(len(prices)):
        for j in range(i + 1, len(prices)):
            L, H = prices[i], prices[j]
            span = H - L
            if span < min_span: continue
            score = 0.0
            for c in all_clusters:
                if abs(c.center - H) < 1e-9 or abs(c.center - L) < 1e-9:
                    score += c.total_conf; continue
                ratio = (c.center - L) / span
                nearest = min(config.std_ratios, key=lambda r: abs(r - ratio))
                error = abs(ratio - nearest)
                if error < config.max_ratio_error:
                    score += c.total_conf * (1 - error / config.max_ratio_error)
            if score > best_score:
                best_score, best_h, best_l = score, H, L
    if best_score <= 0: return None
    levels = retracement_from_leg(best_l, best_h, config.std_ratios)
    return FibResult(best_h=best_h, best_l=best_l, levels=levels, score=best_score)

def _save_fib_csv(symbol: str, result: FibResult):
    header = ["ratio", "price", "best_h", "best_l", "score"]
    rows = [(r, round(p, 6), result.best_h, result.best_l, result.score) for r, p in result.levels]
    dump_csv(f"tmp/{symbol}_fib_levels.csv", header, rows)

# ── Command ──
class ComputeFibRetracement(BaseCommand):
    """手动触发重算 Fib + CSV 落盘。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeFibRetracement"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: int = 0
    end_ts: int = 0

    async def __call__(self, *args, **kwargs) -> Any:
        from timing.engine.cache import GetKlines
        gk = GetKlines(symbol=self.symbol, interval=self.interval, start_ts=self.start_ts or None, end_ts=self.end_ts or None, qos=0)
        await hub.execute(gk)
        klines = await gk.state
        if not klines: return {"error": "no klines"}
        result = app.recompute_fib(self.symbol, self.interval, klines)
        if not result: return {"error": "fit_fib no result"}
        levels_dicts = [{"ratio": r, "price": round(p, 6)} for r, p in result.levels]
        if protocol:
            try: _save_fib_csv(self.symbol, result)
            except Exception: pass
        from timing.analysis.models import FibLinesUpdated
        rev = hub.get_service("timing.CacheEngine").revision(self.symbol, self.interval)
        await hub.emit(FibLinesUpdated(symbol=self.symbol, interval=self.interval, levels=levels_dicts, revision=rev))
        return {"best_h": result.best_h, "best_l": result.best_l, "score": result.score, "levels": levels_dicts}
