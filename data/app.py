"""
DataEngine — K 线数据存储层。

【职责】
  存放和查询所有标的的 K 线数据，供分析服务读取。

【存储结构】
  protocol 链：TableCacheLayer（内存 dict 快读） → DuckDBProtocol（DuckDB 列式落盘）
  数据按 "symbol:interval" 作为 key 分区，如 "159363.OF:1d"
  每个 key 对应一个按 ts 排序的 bar 列表

【对外接口】
  get_klines(symbol, interval, ...) — 同步查询（直接读内存缓存）
  set_klines / append_bars           — 异步写入（写缓存 + 落盘）

【命令入口（CLI 可调用）】
  PushBars          — HTTP 推送新 bar → 写入 + 广播给订阅者
  GetKlines         — 查询 K 线
  IngestKlinesFromFile — 从 parquet/csv 文件批量导入
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
        """构建 protocol 链：TableCacheLayer → DuckDB（TOML 已配 protocol 时跳过）。"""
        if not self.protocol:
            inner = DuckDBProtocol(url=self._db_path)
            cache = TableCacheLayer(**table_schema())
            cache.add_dependency(inner)
            self.add_dependency(cache)
            log.info(f'[数据] 协议链就绪: TableCacheLayer → DuckDB({self._db_path})')
        await super().on_start()

    # ──────────────── 查询 ────────────────

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None,
                   offset: int = None, limit: int = None) -> List[dict]:
        """同步查询 K 线（直接读 TableCacheLayer 的内存缓存，零 IO）。"""
        rows = list(self.protocol.adapter.get(f"{symbol}:{interval}", []))
        if start_ts is not None: rows = [r for r in rows if r["ts"] >= start_ts]
        if end_ts is not None: rows = [r for r in rows if r["ts"] <= end_ts]
        if offset is not None: rows = rows[offset:]
        if limit is not None: rows = rows[:limit]
        return rows

    # ──────────────── 写入 ────────────────

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        """全量覆盖写入（导入数据时用）。"""
        await self.protocol.set(f"{symbol}:{interval}", klines)
        log.info(f'[数据] 写入 {symbol}/{interval} 共{len(klines)}条')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        """增量追加 bars（实时推送时用）。"""
        key = f"{symbol}:{interval}"
        existing = list(self.protocol.adapter.get(key, []))
        normalized = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in bars]
        existing.extend(normalized)
        await self.protocol.set(key, existing)
        log.info(f'[数据] 追加 {symbol}/{interval} +{len(bars)} 总计{len(existing)}条')
