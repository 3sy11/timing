"""DataEngine — K 线数据存储层。使用共享 TimingDuckDBProtocol。"""
import logging
from typing import List
from bollydog.models.service import AppService
from timing.adapters.duckdb import TimingDuckDBProtocol

log = logging.getLogger(__name__)


class DataEngine(AppService):
    domain = "data"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {"PushBars": ["POST", "/api/timing/push_bars"], "ImportKlines": ["POST", "/api/timing/import_klines"],
                      "GetKlinesAPI": ["GET", "/api/data/klines"], "ListSymbols": ["GET", "/api/data/symbols"]}

    def __init__(self, **kwargs):
        self.db: TimingDuckDBProtocol = None
        self._cache: dict[str, list] = {}
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self.db = TimingDuckDBProtocol.shared()
        if not self.db.adapter:
            await self.db.on_start()
        log.info(f'[数据] DuckDB 就绪: {self.db.url}')
        await super().on_start()

    async def on_stop(self) -> None:
        self._cache.clear()
        await super().on_stop()

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None,
                   offset: int = None, limit: int = None) -> List[dict]:
        key = f"{symbol}:{interval}"
        if key in self._cache:
            rows = self._cache[key]
        else:
            where = 'WHERE symbol=? AND "interval"=?'
            params = [symbol, interval]
            if start_ts is not None:
                where += " AND ts>=?"; params.append(start_ts)
            if end_ts is not None:
                where += " AND ts<=?"; params.append(end_ts)
            sql = f"SELECT * FROM klines {where} ORDER BY ts"
            result = self.db.adapter.execute(sql, params).fetchall()
            cols = self.db.columns("klines")
            rows = [dict(zip(cols, r)) for r in result]
            if start_ts is None and end_ts is None:
                self._cache[key] = rows
        if start_ts is not None:
            rows = [r for r in rows if r["ts"] >= start_ts]
        if end_ts is not None:
            rows = [r for r in rows if r["ts"] <= end_ts]
        if offset is not None:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        self.db.adapter.execute('DELETE FROM klines WHERE symbol=? AND "interval"=?', [symbol, interval])
        if klines:
            cols = self.db.columns("klines")
            for k in klines:
                row = [symbol, interval] + [k.get(c, 0) for c in cols[2:]]
                self.db.adapter.execute(
                    f"INSERT INTO klines ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})", row)
        self._cache[f"{symbol}:{interval}"] = klines
        log.info(f'[数据] 写入 {symbol}/{interval} 共{len(klines)}条')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        if bars:
            cols = self.db.columns("klines")
            for b in bars:
                row = [symbol, interval] + [b.get(c, 0) for c in cols[2:]]
                self.db.adapter.execute(
                    f"INSERT INTO klines ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})", row)
        key = f"{symbol}:{interval}"
        if key in self._cache:
            self._cache[key].extend(bars)
        log.info(f'[数据] 追加 {symbol}/{interval} +{len(bars)}')
