"""FibService commands：ComputeFibRetracement。
遵循 SKILL.md：app.recompute_fib + protocol 持久化。"""
import json, time
from typing import Any, ClassVar
from bollydog.globals import app, hub, protocol
from bollydog.models.base import BaseCommand
from timing.engine.cache import GetKlines


class ComputeFibRetracement(BaseCommand):
    """手动触发重算 Fib。"""
    destination: ClassVar[str] = "timing.AnalysisEngine.ComputeFibRetracement"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: int = 0
    end_ts: int = 0

    async def __call__(self, *args, **kwargs) -> Any:
        gk = GetKlines(symbol=self.symbol, interval=self.interval, start_ts=self.start_ts or None, end_ts=self.end_ts or None, qos=0)
        await hub.execute(gk)
        klines = await gk.state
        if not klines: return {"error": "no klines"}
        result = app.recompute_fib(self.symbol, self.interval, klines)
        if not result: return {"error": "fit_fib no result"}
        levels_dicts = [{"ratio": r, "price": round(p, 6)} for r, p in result.levels]
        if protocol:
            try:
                now = int(time.time() * 1000)
                async with protocol.connect() as conn:
                    conn.execute("INSERT INTO fib_results (symbol, interval, computed_at, best_h, best_l, score, levels_json) VALUES (?,?,?,?,?,?,?)",
                                 [self.symbol, self.interval, now, result.best_h, result.best_l, result.score, json.dumps(levels_dicts)])
            except Exception: pass
        from timing.analysis.models import FibLinesUpdated
        rev = hub.get_service("timing.CacheEngine").revision(self.symbol, self.interval)
        await hub.emit(FibLinesUpdated(symbol=self.symbol, interval=self.interval, levels=levels_dicts, revision=rev))
        return {"best_h": result.best_h, "best_l": result.best_l, "score": result.score, "levels": levels_dicts}
