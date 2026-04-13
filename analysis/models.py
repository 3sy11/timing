"""AnalysisEngine：Events + 编排 Handlers。
遵循 SKILL.md：Command 通过 globals.app 调业务方法，通过 globals.protocol 持久化。"""
import json, logging, time
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app, hub, protocol
from bollydog.models.base import BaseCommand, BaseEvent
from timing.engine.cache import GetKlines

log = logging.getLogger(__name__)


class FibLinesUpdated(BaseEvent):
    """Fib 线重算完成事件。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.FibLinesUpdated"
    symbol: str = ""
    interval: str = ""
    levels: List[dict] = Field(default_factory=list)
    revision: int = 0
    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


class FibLevelTouched(BaseEvent):
    """触线发生事件。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.FibLevelTouched"
    symbol: str = ""
    ratio: float = 0.0
    level_price: float = 0.0
    touch_price: float = 0.0
    direction: str = "UNKNOWN"
    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


class OnBarForAnalysis(BaseCommand):
    """subscriber：PushBars → 实时触线检测。通过 app.check_touch 编排。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.OnBarForAnalysis"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        sym, interval, bars = info.get("symbol", ""), info.get("interval", ""), info.get("bars", [])
        if not (sym and bars): return None
        touched = app.check_touch(sym, interval, bars)
        for r, p, price in touched:
            await hub.emit(FibLevelTouched(symbol=sym, ratio=r, level_price=p, touch_price=price))
        log.info(f'[Analysis] OnBar {sym}/{interval} bars={len(bars)} touched={len(touched)}')
        return touched


class OnCacheIngested(BaseCommand):
    """subscriber：OnDataIngested → 批量重算 fib。通过 app.recompute_fib 编排 + protocol 持久化。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.OnCacheIngested"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        sym, interval = info.get("symbol", ""), info.get("interval", "")
        rev = info.get("revision", 0)
        if not sym: return None
        gk = GetKlines(symbol=sym, interval=interval, qos=0)
        await hub.execute(gk)
        klines = await gk.state
        if not klines: return {"error": "no klines in cache"}
        result = app.recompute_fib(sym, interval, klines)
        if not result:
            log.warning(f'[Analysis] fit_fib failed for {sym}/{interval}')
            return {"error": "fit_fib no result"}
        levels_dicts = [{"ratio": r, "price": round(p, 6)} for r, p in result.levels]
        # persist via protocol
        if protocol:
            try:
                now = int(time.time() * 1000)
                async with protocol.connect() as conn:
                    conn.execute("INSERT INTO fib_results (symbol, interval, computed_at, best_h, best_l, score, levels_json) VALUES (?,?,?,?,?,?,?)",
                                 [sym, interval, now, result.best_h, result.best_l, result.score, json.dumps(levels_dicts)])
                log.info(f'[Analysis] persisted fib_results {sym}/{interval} score={result.score:.2f}')
            except Exception as e:
                log.warning(f'[Analysis] persist fib_results failed: {e}')
        await hub.emit(FibLinesUpdated(symbol=sym, interval=interval, levels=levels_dicts, revision=rev))
        return {"symbol": sym, "interval": interval, "revision": rev, "levels_count": len(result.levels), "score": result.score}
