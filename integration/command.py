"""IntegrationService Commands — ImportKlines / PushBars / GetKlines / ListSymbols。"""
import logging, os
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.integration.reader import read_file

log = logging.getLogger(__name__)


class ImportKlines(BaseCommand):
    """从外部文件导入 klines → 写入 warehouse/klines/{symbol}/{interval}/ parquet。"""
    destination: ClassVar[str] = "integration.IntegrationService.ImportKlines"
    path: str = ""
    symbol: str = ""
    interval: str = ""
    filename: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        fname = self.filename or os.path.basename(self.path).rsplit(".", 1)[0] + ".parquet"
        app.write_klines(self.symbol, self.interval, klines, filename=fname)
        log.info(f'[集成] 导入 {self.symbol}/{self.interval} ← {self.path} 共{len(klines)}条')
        return {"symbol": self.symbol, "interval": self.interval, "count": len(klines), "file": fname}


class PushBars(BaseCommand):
    """实时追加 bars → 写入小 parquet 文件。"""
    destination: ClassVar[str] = "integration.IntegrationService.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)

    async def __call__(self, *args, **kwargs) -> Any:
        normalized = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in self.bars]
        app.write_klines(self.symbol, self.interval, normalized)
        return {"symbol": self.symbol, "interval": self.interval, "count": len(normalized)}


class GetKlines(BaseCommand):
    """读取 klines（read_parquet glob）。"""
    destination: ClassVar[str] = "integration.IntegrationService.GetKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = None
    end_ts: int = None
    limit: int = None

    async def __call__(self, *args, **kwargs) -> Any:
        return app.get_klines(self.symbol, self.interval, self.start_ts, self.end_ts, self.limit)


class ListSymbols(BaseCommand):
    """列出 warehouse/klines/ 下所有已有品种。"""
    destination: ClassVar[str] = "integration.IntegrationService.ListSymbols"

    async def __call__(self, *args, **kwargs) -> Any:
        klines_root = os.path.join(app.warehouse_path, "klines")
        if not os.path.isdir(klines_root): return []
        result = []
        for symbol in sorted(os.listdir(klines_root)):
            sym_dir = os.path.join(klines_root, symbol)
            if not os.path.isdir(sym_dir): continue
            for interval in sorted(os.listdir(sym_dir)):
                iv_dir = os.path.join(sym_dir, interval)
                if not os.path.isdir(iv_dir): continue
                files = [f for f in os.listdir(iv_dir) if f.endswith(".parquet")]
                result.append({"symbol": symbol, "interval": interval, "files": len(files)})
        return result
