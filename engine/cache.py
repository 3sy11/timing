"""K 线缓存引擎：内存 dict 存储 + subscribe handler。
handler 完成后 bollydog _publish 自动以 destination 广播，下游可订阅 handler completion。"""
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from bollydog.globals import hub
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService


class OnBar(BaseCommand):
    """subscribe timing.DataEngine.OHLCV → 单 bar 写入 Cache。"""
    destination: ClassVar[str] = "timing.CacheEngine.OnBar"
    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw:
            return None
        sym, interval = raw.get("symbol"), raw.get("interval")
        if not (sym and interval):
            return None
        bar = {k: raw[k] for k in ("open", "high", "low", "close", "volume", "ts") if k in raw}
        hub.get_service("timing.CacheEngine").append_bar(sym, interval, bar)
        return True


class OnDataIngested(BaseCommand):
    """subscribe timing.DataEngine.DataIngested → 批量替换 Cache。
    返回值含 revision/rows/symbol/interval，_publish 自动广播给下游。"""
    destination: ClassVar[str] = "timing.CacheEngine.OnDataIngested"
    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw:
            return None
        sym, interval = raw.get("symbol", ""), raw.get("interval", "")
        klines_dicts = raw.get("klines", [])
        if not (sym and interval and klines_dicts):
            return None
        cache = hub.get_service("timing.CacheEngine")
        rev = cache.replace_klines(sym, interval, klines_dicts)
        return {"revision": rev, "rows": len(klines_dicts), "symbol": sym, "interval": interval}


class GetKlines(BaseCommand):
    destination: ClassVar[str] = "timing.CacheEngine.GetKlines"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    async def __call__(self, *args, **kwargs) -> Any:
        return hub.get_service("timing.CacheEngine").get_klines(self.symbol, self.interval, self.start_ts, self.end_ts)


class CacheEngine(AppService):
    domain = "timing"
    alias = "CacheEngine"
    commands = ["commands"]
    subscribe = {
        "timing.DataEngine.OHLCV": OnBar,
        "timing.DataEngine.DataIngested": OnDataIngested,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store: Dict[Tuple[str, str], List[dict]] = {}
        self._revision: Dict[Tuple[str, str], int] = {}

    def append_bar(self, symbol: str, interval: str, bar: dict) -> None:
        key = (symbol, interval)
        d = {"open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]),
             "close": float(bar["close"]), "volume": float(bar.get("volume", 0)), "ts": int(bar["ts"])}
        self._store.setdefault(key, []).append(d)
        self._store[key].sort(key=lambda x: x["ts"])

    def replace_klines(self, symbol: str, interval: str, klines: List[dict]) -> int:
        key = (symbol, interval)
        self._store[key] = sorted(klines, key=lambda x: x["ts"])
        self._revision[key] = self._revision.get(key, 0) + 1
        return self._revision[key]

    def revision(self, symbol: str, interval: str) -> int:
        return self._revision.get((symbol, interval), 0)

    def get_klines(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[dict]:
        rows = list(self._store.get((symbol, interval), []))
        if start_ts is not None:
            rows = [x for x in rows if x["ts"] >= start_ts]
        if end_ts is not None:
            rows = [x for x in rows if x["ts"] <= end_ts]
        return rows


__all__ = ["CacheEngine", "GetKlines", "OnBar", "OnDataIngested"]
