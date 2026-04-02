"""Clock 抽象：时间控制权。LiveClock 用系统时钟，SimulatedClock 用于回测。
任何引擎中需要当前时间的地方注入 Clock，避免直接 time.time() / datetime.now()。"""
import asyncio, time
from abc import ABC, abstractmethod


class Clock(ABC):
    @abstractmethod
    def now_ms(self) -> int: ...
    def now_sec(self) -> float:
        return self.now_ms() / 1000.0
    @abstractmethod
    async def sleep(self, seconds: float) -> None: ...


class LiveClock(Clock):
    def now_ms(self) -> int:
        return int(time.time() * 1000)
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimulatedClock(Clock):
    """回测时钟：由回放 DataClient 推进 set_time_ms。"""
    def __init__(self, start_ms: int = 0):
        self._time_ms = start_ms
    def now_ms(self) -> int:
        return self._time_ms
    def set_time_ms(self, ts: int) -> None:
        self._time_ms = ts
    def advance_ms(self, delta: int) -> None:
        self._time_ms += delta
    async def sleep(self, seconds: float) -> None:
        pass
