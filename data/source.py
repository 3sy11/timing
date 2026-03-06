"""K线数据源抽象与内存实现。"""
from abc import ABC, abstractmethod
from typing import List, Optional
from .kline import Kline


class KlineSource(ABC):
    """K线数据源抽象。"""

    @abstractmethod
    def get_klines(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[Kline]:
        """取标的、周期在 [start_ts, end_ts] 内的K线，按 ts 升序。"""
        ...


class ListKlineSource(KlineSource):
    """用内存列表提供K线；不按 symbol/interval 区分，仅按时间过滤。"""

    def __init__(self, klines: List[Kline]):
        self._klines = sorted(klines, key=lambda k: k.ts)

    def get_klines(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[Kline]:
        out = self._klines
        if start_ts is not None: out = [k for k in out if k.ts >= start_ts]
        if end_ts is not None: out = [k for k in out if k.ts <= end_ts]
        return out
