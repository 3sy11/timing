"""共享行情数据类型（K 线），纯数据结构。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Kline:
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts: int

OHLCV = Kline
Bar = Kline
