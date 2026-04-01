"""K 线缓存引擎：内存存储；Command 与同模块内定义。"""
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import Field

from bollydog.globals import hub
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService
from timing.models.kline import Kline


class CacheEngine(AppService):
    domain = "timing"
    alias = "CacheEngine"
    commands = ["commands"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store: Dict[Tuple[str, str], List[Kline]] = {}
        self._revision: Dict[Tuple[str, str], int] = {}

    def append_bar(self, symbol: str, interval: str, bar: dict) -> None:
        key = (symbol, interval)
        k = Kline(
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            volume=float(bar.get("volume", 0)),
            ts=int(bar["ts"]),
        )
        self._store.setdefault(key, []).append(k)
        self._store[key].sort(key=lambda x: x.ts)

    def replace_klines(self, symbol: str, interval: str, klines: List[Kline]) -> int:
        key = (symbol, interval)
        self._store[key] = sorted(list(klines), key=lambda x: x.ts)
        self._revision[key] = self._revision.get(key, 0) + 1
        return self._revision[key]

    def revision(self, symbol: str, interval: str) -> int:
        return self._revision.get((symbol, interval), 0)

    def get_klines(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[Kline]:
        key = (symbol, interval)
        rows = list(self._store.get(key, []))
        if start_ts is not None:
            rows = [x for x in rows if x.ts >= start_ts]
        if end_ts is not None:
            rows = [x for x in rows if x.ts <= end_ts]
        return rows

    def get_klines_dicts(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[dict]:
        return [self._kline_to_dict(k) for k in self.get_klines(symbol, interval, start_ts, end_ts)]

    @staticmethod
    def _kline_to_dict(k: Kline) -> dict:
        return {"open": k.open, "high": k.high, "low": k.low, "close": k.close, "volume": k.volume, "ts": k.ts}


class AppendBar(BaseCommand):
    destination: ClassVar[str] = "timing.CacheEngine.AppendBar"
    symbol: str = ""
    interval: str = ""
    bar: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        hub.get_service("timing.CacheEngine").append_bar(self.symbol, self.interval, self.bar)
        return True


class GetKlines(BaseCommand):
    destination: ClassVar[str] = "timing.CacheEngine.GetKlines"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None

    async def __call__(self, *args, **kwargs) -> Any:
        return hub.get_service("timing.CacheEngine").get_klines_dicts(self.symbol, self.interval, self.start_ts, self.end_ts)


class OnBarReceived(BaseCommand):
    """可选：手动订阅 BarEvent 时写入 Cache；默认由 DataEngine 先写 Cache 再 emit，避免与 subscribe 双写。"""
    destination: ClassVar[str] = "timing.CacheEngine.OnBarReceived"

    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw:
            return None
        sym, interval, bar = raw.get("symbol"), raw.get("interval"), raw.get("bar")
        if sym and interval and bar is not None:
            hub.get_service("timing.CacheEngine").append_bar(sym, interval, bar if isinstance(bar, dict) else {})
        return True


__all__ = ["CacheEngine", "AppendBar", "GetKlines", "OnBarReceived"]
