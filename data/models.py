"""DataEngine Command 定义 — PushBars / GetKlines / ImportKlines。"""
import logging
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_file

log = logging.getLogger(__name__)


class PushBars(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)
    replay: bool = False

    async def __call__(self, *args, **kwargs) -> Any:
        normalized = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in self.bars]
        if not self.replay:
            await app.append_bars(self.symbol, self.interval, normalized)
        return {"symbol": self.symbol, "interval": self.interval, "bars": normalized}


class GetKlines(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.GetKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = None
    end_ts: int = None
    offset: int = None
    limit: int = None

    async def __call__(self, *args, **kwargs) -> Any:
        return app.get_klines(self.symbol, self.interval, self.start_ts, self.end_ts, self.offset, self.limit)


class ImportKlines(BaseCommand):
    destination: ClassVar[str] = "data.DataEngine.ImportKlines"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        await app.set_klines(self.symbol, self.interval, klines)
        log.info(f'[数据] 从文件导入 {self.symbol}/{self.interval} 共{len(klines)}条')
        return {"symbol": self.symbol, "interval": self.interval, "count": len(klines)}
