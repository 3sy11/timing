"""内存 ListKlineSource，子 AppService。"""
from typing import List, Optional

from timing.models.kline import Kline
from timing.models.source import ListKlineSource
from bollydog.models.service import AppService


class ListDataClient(AppService):
    domain = "timing"
    alias = "ListDataClient"

    def __init__(self, klines: Optional[List[Kline]] = None, **kwargs):
        super().__init__(**kwargs)
        self._source = ListKlineSource(list(klines or []))

    def set_klines(self, klines: List[Kline]) -> None:
        self._source = ListKlineSource(list(klines))

    def request_klines(self, symbol: str, interval: str, start_ts: Optional[int], end_ts: Optional[int]) -> List[Kline]:
        return self._source.get_klines(symbol, interval, start_ts, end_ts)
