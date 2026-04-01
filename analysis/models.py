"""AnalysisEngine：Command / Event。"""
from typing import Any, ClassVar, List, Tuple

from pydantic import Field

from bollydog.globals import hub
from bollydog.models.base import BaseCommand, BaseEvent
from timing.analysis.algo.fibonacci import DEFAULT_RATIOS, retracement_from_leg
from timing.analysis.algo.swing import find_swing_highs_lows, select_trend_leg
from timing.analysis.algo.touch import check_touch
from timing.engine.cache import GetKlines
from timing.models.kline import Kline


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
        klines = [Kline(**x) for x in raw]
        swings = find_swing_highs_lows(klines, self.left_bars, self.right_bars)
        leg = select_trend_leg(swings, self.direction, klines)
        if not leg:
            return {"error": "no trend leg"}
        levels = retracement_from_leg(leg.low, leg.high, DEFAULT_RATIOS)
        hub.get_service("timing.AnalysisEngine").set_fib_state(self.symbol, self.interval, levels, leg_low=leg.low, leg_high=leg.high)
        return {
            "leg": {
                "start_ts": leg.start_ts,
                "end_ts": leg.end_ts,
                "low": leg.low,
                "high": leg.high,
                "direction": leg.direction,
            },
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
    destination: ClassVar[str] = "timing.AnalysisEngine.OnBarForAnalysis"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw:
            return None
        sym, interval, bar = raw.get("symbol"), raw.get("interval"), raw.get("bar") or {}
        price = float(bar.get("close", 0))
        eng = hub.get_service("timing.AnalysisEngine")
        levels = eng.get_levels(sym, interval)
        if not levels:
            return []
        det = eng.get_touch_detector(sym, interval)
        touched = det.check(price, levels, eng.touch_tolerance)
        for r, p in touched:
            await hub.emit(FibLevelTouched(symbol=sym, ratio=r, level_price=p, touch_price=price))
        return [(r, p) for r, p in touched]
