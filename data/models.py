"""DataEngine：Command。_publish 自动广播，订阅者从 state 取数据。"""
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_klines


class PushBars(BaseCommand):
    """HTTP 推送 bars，_publish 广播给订阅者。"""
    destination: ClassVar[str] = "timing.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)
    async def __call__(self, *args, **kwargs) -> Any:
        processed = []
        for bar in self.bars:
            processed.append({"open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]),
                              "close": float(bar["close"]), "volume": float(bar.get("volume", 0)), "ts": int(bar["ts"])})
        return {"symbol": self.symbol, "interval": self.interval, "bars": processed}


class IngestParquetFile(BaseCommand):
    """读 Parquet，_publish 广播给订阅者。"""
    destination: ClassVar[str] = "timing.DataEngine.IngestParquetFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_klines(self.path)
        return {"symbol": self.symbol, "interval": self.interval, "klines": klines}
