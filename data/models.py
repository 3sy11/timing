"""
DataEngine 的命令定义 — CLI 和 HTTP 都通过这些命令与 DataEngine 交互。

【路由机制】
  每个命令的 destination 格式为 "domain.alias.CommandName"
  框架根据 destination 找到对应的 AppService 实例，在其上下文中执行 __call__
  __call__ 中的 `app` 就是 DataEngine 实例
"""
import logging
from typing import Any, ClassVar, List
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.data.clients.file import read_file

log = logging.getLogger(__name__)


class PushBars(BaseCommand):
    """
    推送新 bar 数据。

    生产用：外部 HTTP 调用 → 写入 DataEngine → 自动广播给所有订阅了 PushBars 的分析服务
    回测用：replay=True 时不写入，只作为事件载体触发分析服务的 on_bar
    """
    destination: ClassVar[str] = "data.DataEngine.PushBars"
    symbol: str = ""
    interval: str = ""
    bars: List[dict] = Field(default_factory=list)
    replay: bool = False

    async def __call__(self, *args, **kwargs) -> Any:
        normalized = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in self.bars]
        if not self.replay:
            await app.append_bars(self.symbol, self.interval, normalized)
        return {"symbol": self.symbol, "interval": self.interval, "bars": normalized}


class GetKlines(BaseCommand):
    """查询 K 线数据（通过命令分派，解耦对 DataEngine 的直接引用）。"""
    destination: ClassVar[str] = "data.DataEngine.GetKlines"
    symbol: str = ""
    interval: str = ""
    start_ts: int = None
    end_ts: int = None
    offset: int = None
    limit: int = None

    async def __call__(self, *args, **kwargs) -> Any:
        return app.get_klines(self.symbol, self.interval, self.start_ts, self.end_ts,
                              self.offset, self.limit)


class IngestKlinesFromFile(BaseCommand):
    """从 parquet/csv 文件批量导入 K 线数据到 DataEngine。"""
    destination: ClassVar[str] = "data.DataEngine.IngestKlinesFromFile"
    path: str = ""
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        klines = read_file(self.path)
        await app.set_klines(self.symbol, self.interval, klines)
        log.info(f'[数据] 从文件导入 {self.symbol}/{self.interval} 共{len(klines)}条')
        return {"symbol": self.symbol, "interval": self.interval, "rows": len(klines)}
