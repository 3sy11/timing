"""AnalysisEngine：Command / Event / Handler。"""
from typing import Any, ClassVar, List, Tuple
from pydantic import Field
from bollydog.globals import hub
from bollydog.models.base import BaseCommand, BaseEvent
from timing.analysis.algo.fibonacci import DEFAULT_RATIOS, retracement_from_leg
from timing.analysis.algo.swing import find_swing_highs_lows, select_trend_leg
from timing.analysis.algo.touch import check_touch
from timing.engine.cache import GetKlines


class FibLevelTouched(BaseEvent):
    destination: ClassVar[str] = "timing.AnalysisEngine.FibLevelTouched"
    symbol: str = ""
    ratio: float = 0.0
    level_price: float = 0.0
    touch_price: float = 0.0
    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


class ComputeFibRetracement(BaseCommand):
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeFibRetracement"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: int = 0
    end_ts: int = 0
    direction: str = "latest"
    left_bars: int = 5
    right_bars: int = 5

    async def __call__(self, *args, **kwargs) -> Any:
        gk = GetKlines(symbol=self.symbol, interval=self.interval, start_ts=self.start_ts, end_ts=self.end_ts, qos=0)
        await hub.execute(gk)
        raw = await gk.state
        if not raw:
            return {"error": "no klines"}
        swings = find_swing_highs_lows(raw, self.left_bars, self.right_bars)
        leg = select_trend_leg(swings, self.direction, raw)
        if not leg:
            return {"error": "no trend leg"}
        levels = retracement_from_leg(leg.low, leg.high, DEFAULT_RATIOS)
        hub.get_service("timing.AnalysisEngine").set_fib_state(self.symbol, self.interval, levels, leg_low=leg.low, leg_high=leg.high)
        return {
            "leg": {"start_ts": leg.start_ts, "end_ts": leg.end_ts, "low": leg.low, "high": leg.high, "direction": leg.direction},
            "levels": [(r, round(p, 6)) for r, p in levels],
        }


class FeedPrice(BaseCommand):
    destination: ClassVar[str] = "timing.AnalysisEngine.FeedPrice"
    symbol: str = ""
    price: float = 0.0
    levels: List[Tuple[float, float]] = Field(default_factory=list)
    tolerance: float = 0.5

    async def __call__(self, *args, **kwargs) -> Any:
        touched = check_touch(self.price, list(self.levels), self.tolerance)
        out = []
        for r, p in touched:
            await hub.emit(FibLevelTouched(symbol=self.symbol, ratio=r, level_price=p, touch_price=self.price))
            out.append((r, p))
        return out


class OnBarForAnalysis(BaseCommand):
    """subscribe timing.CacheEngine.OnBar（Cache 已写入后再分析）：从 Cache 取最新 close 做触线。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.OnBarForAnalysis"

    async def __call__(self, *args, **kwargs) -> Any:
        ob = self.get_event(-1)
        if not ob:
            return None
        evs = (ob.get("data") or {}).get("events") or []
        if not evs:
            return None
        ohlcv = evs[-1]
        sym, interval = ohlcv.get("symbol"), ohlcv.get("interval")
        if not (sym and interval):
            return None
        rows = hub.get_service("timing.CacheEngine").get_klines(sym, interval)
        if not rows:
            return []
        price = float(rows[-1]["close"])
        eng = hub.get_service("timing.AnalysisEngine")
        levels = eng.get_levels(sym, interval)
        if not levels:
            return []
        det = eng.get_touch_detector(sym, interval)
        touched = det.check(price, levels, eng.touch_tolerance)
        for r, p in touched:
            await hub.emit(FibLevelTouched(symbol=sym, ratio=r, level_price=p, touch_price=price))
        return [(r, p) for r, p in touched]


class OnCacheIngested(BaseCommand):
    """subscribe timing.CacheEngine.OnDataIngested（_publish）→ 批量重算 fib。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.OnCacheIngested"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw:
            return None
        state = raw.get("state", [])
        if not state or state[0] != "FINISHED" or isinstance(state[1], str):
            return None
        info = state[1]
        sym, interval = info.get("symbol", ""), info.get("interval", "")
        if not (sym and interval):
            return None
        gk = GetKlines(symbol=sym, interval=interval, qos=0)
        await hub.execute(gk)
        kline_dicts = await gk.state
        if not kline_dicts:
            return {"error": "no klines in cache"}
        swings = find_swing_highs_lows(kline_dicts, 5, 5)
        leg = select_trend_leg(swings, "latest", kline_dicts)
        if not leg:
            return {"error": "no trend leg"}
        levels = retracement_from_leg(leg.low, leg.high, DEFAULT_RATIOS)
        hub.get_service("timing.AnalysisEngine").set_fib_state(sym, interval, levels, leg_low=leg.low, leg_high=leg.high)
        return {"symbol": sym, "interval": interval, "revision": info.get("revision"), "levels_count": len(levels)}
