"""LiveExchange — 真实交易所适配器占位。

后续实现 HTTP/WS 连接到 Binance/OKX 等交易所。
接口与 SimExchange 一致，通过配置切换即可。
"""
from timing.models.order import Order, FillResult
from timing.models.account import Account


class LiveExchange:

    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def submit_order(self, order: Order, bar: dict = None) -> FillResult:
        raise NotImplementedError("LiveExchange.submit_order 尚未实现")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("LiveExchange.cancel_order 尚未实现")

    def get_balance(self) -> Account:
        raise NotImplementedError("LiveExchange.get_balance 尚未实现")
