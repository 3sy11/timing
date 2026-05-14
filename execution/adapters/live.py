"""LiveExchangeProtocol — 真实交易所适配器占位。

后续实现 HTTP/WS 连接到 Binance/OKX 等交易所。
接口与 SimExchangeProtocol 一致，通过 TOML 切换 module 即可。
"""
from timing.execution.adapters.base import ExchangeProtocol
from timing.models.order import Order, FillResult
from timing.models.account import Account


class LiveExchangeProtocol(ExchangeProtocol):

    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = "", **kwargs):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        super().__init__(**kwargs)

    async def submit_order(self, order: Order, bar: dict = None) -> FillResult:
        raise NotImplementedError("LiveExchangeProtocol.submit_order 尚未实现")

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("LiveExchangeProtocol.cancel_order 尚未实现")

    async def get_balance(self) -> Account:
        raise NotImplementedError("LiveExchangeProtocol.get_balance 尚未实现")
