"""行情接入：挂载 Parquet Client，HTTP router_mapping，protocol 持久化 klines。"""
import logging
from timing.data.clients.file_parquet import FileParquetDataClient
from bollydog.models.service import AppService

log = logging.getLogger(__name__)

_DATA_TABLES = """
CREATE TABLE IF NOT EXISTS klines (
    symbol VARCHAR NOT NULL, interval VARCHAR NOT NULL DEFAULT '',
    ts BIGINT NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE DEFAULT 0
);
"""


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models", "command"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestParquetFile": ["POST", "/api/timing/ingest_parquet_file"],
    }

    def __init__(self, protocol=None, **kwargs):
        super().__init__(protocol=protocol, **kwargs)
        self.parquet_client = FileParquetDataClient()
        self.add_dependency(self.parquet_client)

    async def on_started(self) -> None:
        await super().on_started()
        if self.protocol:
            try:
                async with self.protocol.connect() as conn:
                    for stmt in _DATA_TABLES.strip().split(';'):
                        stmt = stmt.strip()
                        if stmt: conn.execute(stmt)
                log.info('[DataEngine] duckdb tables ready')
            except Exception as e:
                log.warning(f'[DataEngine] duckdb init failed: {e}')
