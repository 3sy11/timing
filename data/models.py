"""DataEngine Command：PushBars / IngestKlinesFromFile 直接操作 app(DataEngine)。"""
import logging
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_file

log = logging.getLogger(__name__)


class PushBars(BaseCommand):
    """HTTP 推送 bars → 写入 DataEngine protocol 缓存 → _publish 广播给订阅者。
    replay=True 时跳过 append_bars，仅返回结果并触发 _publish 广播链。
    """
    destination: ClassVar[str] = "data.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)
    replay: bool = False
    async def __call__(self, *args, **kwargs) -> Any:
        processed = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in self.bars]
        if not self.replay:
            await app.append_bars(self.symbol, self.interval, processed)
            log.info(f'[Data] PushBars {self.symbol}/{self.interval} +{len(processed)} total={len(app.get_klines(self.symbol, self.interval))}')
        return {"symbol": self.symbol, "interval": self.interval, "bars": processed}


class IngestKlinesFromFile(BaseCommand):
    """从 parquet/csv 读入 K 线 → 写入 DataEngine protocol 缓存。"""
    destination: ClassVar[str] = "data.DataEngine.IngestKlinesFromFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        await app.set_klines(self.symbol, self.interval, klines)
        log.info(f'[Data] IngestKlines {self.symbol}/{self.interval} rows={len(klines)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": klines}
