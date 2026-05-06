"""DataEngine — TableCacheLayer(DuckDBProtocol) 内存快读 + DuckDB 列式落盘。

TOML 独立部署 / 作为子服务默认协议链均可。
_load_commands 由 load_from_config 全局统一调用，on_start 不再重复。
"""
import logging
from typing import List
from bollydog.models.service import AppService
from timing.data.config import DataConfig

log = logging.getLogger(__name__)


class DataEngine(AppService):
    domain = "data"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestKlinesFromFile": ["POST", "/api/timing/ingest_klines_from_file"],
    }

    def __init__(self, db_path: str = None, **kwargs):
        self._db_path = db_path or DataConfig().db_path
        super().__init__(**kwargs)

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None,
                   offset: int = None, limit: int = None) -> List[dict]:
        rows = list(self.protocol.adapter.get(f"{symbol}:{interval}", []))
        if start_ts is not None: rows = [x for x in rows if x["ts"] >= start_ts]
        if end_ts is not None: rows = [x for x in rows if x["ts"] <= end_ts]
        if offset is not None: rows = rows[offset:]
        if limit is not None: rows = rows[:limit]
        return rows

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        await self.protocol.set(f"{symbol}:{interval}", klines)
        log.info(f'[Data] set_klines {symbol}/{interval} rows={len(klines)}')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        key = f"{symbol}:{interval}"
        existing = list(self.protocol.adapter.get(key, []))
        processed = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in bars]
        existing.extend(processed)
        await self.protocol.set(key, existing)
        log.info(f'[Data] append_bars {symbol}/{interval} +{len(bars)} total={len(existing)}')
