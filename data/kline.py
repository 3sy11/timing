"""K线/OHLCV 模型。"""
from typing import Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class OHLCV:
    """单根K线：开高低收量、时间戳（毫秒）。"""
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts: int  # ms


Kline = OHLCV
