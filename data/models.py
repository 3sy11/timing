"""DataEngine：Bar/Ingest 事件与 Command。"""
from typing import Any, ClassVar, List

from pydantic import Field

from bollydog.globals import hub
from bollydog.models.base import BaseCommand, BaseEvent
from timing.models.kline import Kline


class BarEvent(BaseEvent):
    destination: ClassVar[str] = "timing.DataEngine.BarEvent"
    symbol: str = ""
    interval: str = ""
    bar: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


class IngestCompleted(BaseEvent):
    destination: ClassVar[str] = "timing.DataEngine.IngestCompleted"
    symbol: str = ""
    interval: str = ""
    source: str = "file"
    rows: int = 0
    revision: int = 0

    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


def _kline_to_dict(k: Kline) -> dict:
    return {"open": k.open, "high": k.high, "low": k.low, "close": k.close, "volume": k.volume, "ts": k.ts}


class RequestKlines(BaseCommand):
    destination: ClassVar[str] = "timing.DataEngine.RequestKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = 0
    end_ts: int = 0

    async def __call__(self, *args, **kwargs) -> Any:
        de = hub.get_service("timing.DataEngine")
        cache = hub.get_service("timing.CacheEngine")
        klines: List[Kline] = de.list_client.request_klines(self.symbol, self.interval, self.start_ts, self.end_ts)
        out = []
        for k in klines:
            d = _kline_to_dict(k)
            cache.append_bar(self.symbol, self.interval, d)
            await hub.emit(BarEvent(symbol=self.symbol, interval=self.interval, bar=d))
            out.append(d)
        return out


class PushBars(BaseCommand):
    destination: ClassVar[str] = "timing.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)

    async def __call__(self, *args, **kwargs) -> Any:
        cache = hub.get_service("timing.CacheEngine")
        for bar in self.bars:
            cache.append_bar(self.symbol, self.interval, bar)
            await hub.emit(BarEvent(symbol=self.symbol, interval=self.interval, bar=bar))
        return {"ok": True, "count": len(self.bars)}


class IngestParquetFile(BaseCommand):
    destination: ClassVar[str] = "timing.DataEngine.IngestParquetFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        de = hub.get_service("timing.DataEngine")
        klines = de.parquet_client.read_klines(self.path)
        cache = hub.get_service("timing.CacheEngine")
        rev = cache.replace_klines(self.symbol, self.interval, klines)
        await hub.emit(
            IngestCompleted(symbol=self.symbol, interval=self.interval, source="file", rows=len(klines), revision=rev)
        )
        return {"ok": True, "rows": len(klines), "revision": rev}
