"""ExchangeProtocol — 交易所适配器抽象基类。

继承 bollydog Protocol，定义撮合接口。
作为 Broker protocol 链最外层，delegate KV 操作给 inner protocol（CacheLayer → SQLiteProtocol）。

TOML 链：ExchangeProtocol → CacheLayer → SQLiteProtocol
"""
import abc
from bollydog.models.protocol import Protocol
from timing.models.order import Order, FillResult
from timing.models.account import Account


class ExchangeProtocol(Protocol, abstract=True):
    """交易所协议抽象基类 — 子类实现 SimExchangeProtocol / LiveExchangeProtocol。"""

    @abc.abstractmethod
    async def submit_order(self, order: Order, bar: dict = None) -> FillResult:
        """提交订单进行撮合，返回成交结果。"""

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """取消挂单，返回是否成功。"""

    @abc.abstractmethod
    async def get_balance(self) -> Account:
        """查询当前账户余额。"""

    async def get(self, key: str):
        return await self.protocol.get(key) if self.protocol else None

    async def set(self, key: str, value, ttl: int = None):
        if self.protocol: await self.protocol.set(key, value, ttl=ttl)

    async def remove(self, key: str):
        if self.protocol: await self.protocol.remove(key)

    async def exists(self, key: str) -> bool:
        return await self.protocol.exists(key) if self.protocol else False

    async def keys(self, pattern: str = '*') -> list:
        return await self.protocol.keys(pattern) if self.protocol else []
