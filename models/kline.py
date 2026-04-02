"""OHLCV：BaseCommand 子类，可在 bollydog 总线上传递。"""
from typing import Any, ClassVar
from bollydog.models.base import BaseCommand


class OHLCV(BaseCommand):
    """单根 K 线数据，同时是总线消息。__call__ 直接 set_result(True)。"""
    destination: ClassVar[str] = "timing.DataEngine.OHLCV"
    symbol: str = ""
    interval: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    ts: int = 0

    async def __call__(self, *args, **kwargs) -> Any:
        self.state.set_result(True)


Bar = OHLCV
Kline = OHLCV
