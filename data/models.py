"""DataEngine Command：PushBars / IngestKlinesFromFile / SetSymbolConfig 直接操作 app(DataEngine)。"""
import logging
from typing import Any, ClassVar, Dict, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_file

log = logging.getLogger(__name__)


class PushBars(BaseCommand):
    """HTTP 推送 bars → 写入 DataEngine protocol 缓存 → _publish 广播给订阅者。"""
    destination: ClassVar[str] = "timing.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)
    async def __call__(self, *args, **kwargs) -> Any:
        processed = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in self.bars]
        await app.append_bars(self.symbol, self.interval, processed)
        log.info(f'[Data] PushBars {self.symbol}/{self.interval} +{len(processed)} total={len(app.get_klines(self.symbol, self.interval))}')
        return {"symbol": self.symbol, "interval": self.interval, "bars": processed}


class IngestKlinesFromFile(BaseCommand):
    """从 parquet/csv 读入 K 线 → 写入 DataEngine protocol 缓存。"""
    destination: ClassVar[str] = "timing.DataEngine.IngestKlinesFromFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        await app.set_klines(self.symbol, self.interval, klines)
        log.info(f'[Data] IngestKlines {self.symbol}/{self.interval} rows={len(klines)}')
        return {"symbol": self.symbol, "interval": self.interval, "klines": klines}


class SetSymbolConfig(BaseCommand):
    """设置 symbol/interval 的覆盖参数（JSON），下游服务 merge 默认配置后使用。"""
    destination: ClassVar[str] = "timing.DataEngine.SetSymbolConfig"
    symbol: str = ""
    interval: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.symbol and self.interval): return None
        await app.set_symbol_config(self.symbol, self.interval, self.config)
        log.info(f'[Data] SetSymbolConfig {self.symbol}/{self.interval} keys={list(self.config.keys())}')
        return {"symbol": self.symbol, "interval": self.interval, "config": self.config}
