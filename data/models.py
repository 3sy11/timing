"""DataEngine Commands — 数据写入 / 查询命令。"""
import logging
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_file

log = logging.getLogger(__name__)


class PushBars(BaseCommand):
    """HTTP 推送 bars → 写入 DataEngine → _publish 广播给 subscriber。
    replay=True 跳过写入，仅构造结果触发广播链。
    """
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
            log.info(f'[Data] PushBars {self.symbol}/{self.interval} +{len(normalized)}')
        return {"symbol": self.symbol, "interval": self.interval, "bars": normalized}


class GetKlines(BaseCommand):
    """查询 klines — 通过命令分派解耦对 DataEngine 的直接引用。"""
    destination: ClassVar[str] = "data.DataEngine.GetKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = None
    end_ts: int = None
    offset: int = None
    limit: int = None

    async def __call__(self, *args, **kwargs) -> Any:
        return app.get_klines(self.symbol, self.interval, self.start_ts, self.end_ts,
                              self.offset, self.limit)


class IngestKlinesFromFile(BaseCommand):
    """从 parquet/csv 文件导入 K 线数据。"""
    destination: ClassVar[str] = "data.DataEngine.IngestKlinesFromFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        await app.set_klines(self.symbol, self.interval, klines)
        log.info(f'[Data] IngestKlines {self.symbol}/{self.interval} rows={len(klines)}')
        return {"symbol": self.symbol, "interval": self.interval, "rows": len(klines)}
