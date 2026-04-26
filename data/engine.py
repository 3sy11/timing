"""DataEngine：TableCacheLayer(DuckDBProtocol) — 内存快读 + DuckDB 列式落盘，冷启动自动恢复。"""
import logging
from typing import List
from bollydog.adapters.sqlalchemy import DuckDBProtocol
from bollydog.adapters.composite import TableCacheLayer
from bollydog.models.service import AppService
from timing.models.kline import KLINE_COLUMNS, KLINE_KEY_DEFS, kline_ddl
from timing.data.config import DataConfig

log = logging.getLogger(__name__)


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestKlinesFromFile": ["POST", "/api/timing/ingest_klines_from_file"],
    }

    def __init__(self, config: DataConfig = None, **kwargs):
        cfg = config or DataConfig()
        inner = DuckDBProtocol(url=cfg.db_path)
        proto = TableCacheLayer(protocol=inner, table='klines',
            key_columns=[k for k, _ in KLINE_KEY_DEFS],
            value_columns=KLINE_COLUMNS,
            sort_by='ts', ddl=kline_ddl(), flush_threshold=1)
        super().__init__(protocol=proto, **kwargs)
        self.add_dependency(proto)
        self._inner_db = inner

    # ═══════ klines CRUD ═══════

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None) -> List[dict]:
        rows = list(self.protocol.adapter.get(f"{symbol}:{interval}", []))
        if start_ts is not None: rows = [x for x in rows if x["ts"] >= start_ts]
        if end_ts is not None: rows = [x for x in rows if x["ts"] <= end_ts]
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
