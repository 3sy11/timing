"""IntegrationService — 数据集成服务。Parquet 追加写入 + DuckDB read_parquet 读取。"""
import os, logging, time
from typing import List
import duckdb
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class IntegrationService(AppService):
    domain = "integration"
    alias = "IntegrationService"
    commands = ["timing.integration.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        super().__init__(**kwargs)

    def klines_dir(self, symbol: str, interval: str) -> str:
        return os.path.join(self.warehouse_path, "klines", symbol, interval)

    async def on_start(self) -> None:
        log.info(f'[集成] warehouse={self.warehouse_path}')
        await super().on_start()

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None, limit: int = None) -> List[dict]:
        """DuckDB read_parquet glob 读取 klines。"""
        d = self.klines_dir(symbol, interval)
        if not os.path.isdir(d): return []
        pattern = os.path.join(d, "*.parquet")
        where_parts, params = [], {}
        if start_ts is not None:
            where_parts.append("ts >= $start_ts"); params["start_ts"] = start_ts
        if end_ts is not None:
            where_parts.append("ts <= $end_ts"); params["end_ts"] = end_ts
        where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = f"SELECT DISTINCT ON (ts) * FROM read_parquet('{pattern}'){where} ORDER BY ts"
        if limit: sql += f" LIMIT {limit}"
        with duckdb.connect() as conn:
            result = conn.execute(sql, params).fetchall()
            cols = [desc[0] for desc in conn.description]
        return [dict(zip(cols, r)) for r in result]

    def write_klines(self, symbol: str, interval: str, klines: List[dict], filename: str = None):
        """DuckDB COPY 写入 parquet 文件。"""
        if not klines: return
        d = self.klines_dir(symbol, interval)
        os.makedirs(d, exist_ok=True)
        fname = filename or f"{int(time.time() * 1000)}.parquet"
        out_path = os.path.join(d, fname)
        with duckdb.connect() as conn:
            conn.execute("CREATE TEMP TABLE _buf (symbol VARCHAR, \"interval\" VARCHAR, ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)")
            for k in klines:
                conn.execute("INSERT INTO _buf VALUES (?,?,?,?,?,?,?,?)",
                             [symbol, interval, int(k["ts"]), float(k["open"]), float(k["high"]),
                              float(k["low"]), float(k["close"]), float(k.get("volume", 0))])
            conn.execute(f"COPY _buf TO '{out_path}' (FORMAT PARQUET)")
        log.info(f'[集成] 写入 {out_path} {len(klines)}条')
