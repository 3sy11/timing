"""DataEngine — K 线数据层，TableCacheLayer(DuckDB) 内存快读 + 列式落盘。

protocol 链由 on_start 从 Kline schema 自动构建：
  TableCacheLayer(key=symbol:interval, sort=ts) → DuckDBProtocol(file)
"""
import os, logging
from typing import List
from bollydog.models.service import AppService
from bollydog.adapters.composite import TableCacheLayer
from bollydog.adapters.sqlalchemy import DuckDBProtocol
from timing.models.kline import table_schema

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.environ.get("TIMING_DATA_DB_PATH", "cache/data.duckdb")


class DataEngine(AppService):
    domain = "data"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestKlinesFromFile": ["POST", "/api/timing/ingest_klines_from_file"],
    }

    def __init__(self, db_path: str = None, **kwargs):
        self._db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        # 从 Kline schema 构建 protocol 链（TOML 已配置时跳过）
        if not self.protocol:
            inner = DuckDBProtocol(url=self._db_path)
            self.protocol = TableCacheLayer(**table_schema(), protocol=inner)
            log.info(f'[DataEngine] protocol: DuckDB({self._db_path}) → TableCacheLayer')
        await super().on_start()

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None,
                   offset: int = None, limit: int = None) -> List[dict]:
        rows = list(self.protocol.adapter.get(f"{symbol}:{interval}", []))
        if start_ts is not None: rows = [r for r in rows if r["ts"] >= start_ts]
        if end_ts is not None: rows = [r for r in rows if r["ts"] <= end_ts]
        if offset is not None: rows = rows[offset:]
        if limit is not None: rows = rows[:limit]
        return rows

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        await self.protocol.set(f"{symbol}:{interval}", klines)
        log.info(f'[Data] set_klines {symbol}/{interval} rows={len(klines)}')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        key = f"{symbol}:{interval}"
        existing = list(self.protocol.adapter.get(key, []))
        normalized = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in bars]
        existing.extend(normalized)
        await self.protocol.set(key, existing)
        log.info(f'[Data] append_bars {symbol}/{interval} +{len(bars)} total={len(existing)}')
