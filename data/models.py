"""DataEngine：Command / Event。Command 只 emit，不直接操作 CacheEngine。
实时路径 emit OHLCV；批量路径 emit DataIngested。"""
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import hub
from bollydog.models.base import BaseCommand, BaseEvent
from timing.models.kline import OHLCV


class DataIngested(BaseEvent):
    destination: ClassVar[str] = "timing.DataEngine.DataIngested"
    symbol: str = ""
    interval: str = ""
    source: str = "file"
    klines: List[dict] = Field(default_factory=list)
    async def __call__(self, *args, **kwargs):
        return await super().__call__(*args, **kwargs)


class PushBars(BaseCommand):
    destination: ClassVar[str] = "timing.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)

    async def __call__(self, *args, **kwargs) -> Any:
        for bar in self.bars:
            await hub.emit(OHLCV(symbol=self.symbol, interval=self.interval,
                                 open=float(bar["open"]), high=float(bar["high"]),
                                 low=float(bar["low"]), close=float(bar["close"]),
                                 volume=float(bar.get("volume", 0)), ts=int(bar["ts"])))
        return {"ok": True, "count": len(self.bars)}


class IngestParquetFile(BaseCommand):
    destination: ClassVar[str] = "timing.DataEngine.IngestParquetFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        de = hub.get_service("timing.DataEngine")
        klines_dicts = de.parquet_client.read_klines(self.path)
        await hub.emit(DataIngested(symbol=self.symbol, interval=self.interval, source="file", klines=klines_dicts))
        return {"ok": True, "rows": len(klines_dicts)}
