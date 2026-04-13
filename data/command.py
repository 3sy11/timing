"""DataEngine 可独立调用的计算命令。
Jupyter 用法：
    cmd = ReadParquetKlines(path="/path/to/parquet_dir")
    result = await hub.execute(cmd); data = await cmd.state
或无 hub：
    from timing.data.clients.file_parquet import read_klines
    klines = read_klines("/path/to/parquet_dir")
"""
from typing import Any, ClassVar
from bollydog.globals import protocol
from bollydog.models.base import BaseCommand
from timing.data.clients.file_parquet import read_klines


class ReadParquetKlines(BaseCommand):
    """读取 Parquet → 标准 Kline list[dict] + protocol 持久化。"""
    destination: ClassVar[str] = "timing.DataEngine.ReadParquetKlines"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_klines(self.path)
        if protocol and self.symbol:
            from timing.data.models import _persist_klines
            _persist_klines(self.symbol, self.interval, klines)
        return {"klines": klines}
