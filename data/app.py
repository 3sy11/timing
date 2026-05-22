"""DataEngine — K 线数据存储层。直接使用 DuckDB 存取 K 线数据。"""
import os, logging, duckdb
from typing import List
from bollydog.models.service import AppService
from timing.models.kline import kline_ddl, KEY_COLUMNS, VALUE_COLUMNS, ALL_COLUMNS

log = logging.getLogger(__name__)
DEFAULT_DB_PATH = os.environ.get("TIMING_DATA_DB_PATH", "cache/data.duckdb")


class DataEngine(AppService):
    domain = "data"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {"PushBars": ["POST", "/api/timing/push_bars"], "ImportKlines": ["POST", "/api/timing/import_klines"]}

    def __init__(self, db_path: str = None, **kwargs):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._conn = None
        self._cache: dict[str, list] = {}
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = duckdb.connect(self._db_path)
        self._conn.execute(kline_ddl())
        log.info(f'[数据] DuckDB 就绪: {self._db_path}')
        await super().on_start()

    async def on_stop(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
        self._cache.clear()
        await super().on_stop()

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None,
                   offset: int = None, limit: int = None) -> List[dict]:
        key = f"{symbol}:{interval}"
        if key in self._cache:
            rows = self._cache[key]
        else:
            where = "WHERE symbol=? AND interval=?"
            params = [symbol, interval]
            if start_ts is not None: where += " AND ts>=?"; params.append(start_ts)
            if end_ts is not None: where += " AND ts<=?"; params.append(end_ts)
            sql = f"SELECT {', '.join(VALUE_COLUMNS)} FROM klines {where} ORDER BY ts"
            result = self._conn.execute(sql, params).fetchall()
            rows = [dict(zip(VALUE_COLUMNS, r)) for r in result]
            if start_ts is None and end_ts is None:
                self._cache[key] = rows
        if start_ts is not None: rows = [r for r in rows if r["ts"] >= start_ts]
        if end_ts is not None: rows = [r for r in rows if r["ts"] <= end_ts]
        if offset is not None: rows = rows[offset:]
        if limit is not None: rows = rows[:limit]
        return rows

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        self._conn.execute("DELETE FROM klines WHERE symbol=? AND interval=?", [symbol, interval])
        if klines:
            rows = [[symbol, interval] + [k.get(c, 0) for c in VALUE_COLUMNS] for k in klines]
            self._conn.executemany(f"INSERT INTO klines ({', '.join(ALL_COLUMNS)}) VALUES ({', '.join(['?'] * len(ALL_COLUMNS))})", rows)
        self._cache[f"{symbol}:{interval}"] = klines
        log.info(f'[数据] 写入 {symbol}/{interval} 共{len(klines)}条')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        if bars:
            rows = [[symbol, interval] + [b.get(c, 0) for c in VALUE_COLUMNS] for b in bars]
            self._conn.executemany(f"INSERT INTO klines ({', '.join(ALL_COLUMNS)}) VALUES ({', '.join(['?'] * len(ALL_COLUMNS))})", rows)
        key = f"{symbol}:{interval}"
        if key in self._cache:
            self._cache[key].extend(bars)
        log.info(f'[数据] 追加 {symbol}/{interval} +{len(bars)}')
