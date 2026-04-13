"""DataEngine：Command。_publish 自动广播。
遵循 SKILL.md：IngestParquetFile 通过 protocol 持久化 klines。"""
import logging
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import protocol
from bollydog.models.base import BaseCommand
from timing.data.clients.file_parquet import read_klines

log = logging.getLogger(__name__)


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


def _persist_klines(sym: str, interval: str, klines: List[dict]):
    """通过 protocol 将 klines 持久化到 duckdb。"""
    if not protocol: return
    try:
        import duckdb as _duckdb
        conn = _duckdb.connect(protocol.url)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS klines (symbol VARCHAR, interval VARCHAR DEFAULT '', ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE DEFAULT 0)")
            conn.execute("DELETE FROM klines WHERE symbol = ? AND interval = ?", [sym, interval])
            conn.executemany("INSERT INTO klines (symbol, interval, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
                             [(sym, interval, k["ts"], k["open"], k["high"], k["low"], k["close"], k.get("volume", 0)) for k in klines])
            log.info(f'[Data] persisted {len(klines)} klines {sym}/{interval}')
        finally:
            conn.close()
    except Exception as e:
        log.warning(f'[Data] persist klines failed: {e}')


class IngestParquetFile(BaseCommand):
    """读 Parquet + protocol 持久化，_publish 广播给订阅者。"""
    destination: ClassVar[str] = "timing.DataEngine.IngestParquetFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_klines(self.path)
        _persist_klines(self.symbol, self.interval, klines)
        return {"symbol": self.symbol, "interval": self.interval, "klines": klines}
