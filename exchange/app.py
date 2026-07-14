"""ExchangeService — 交易所服务，提供撮合接口供执行模块调用。"""
import logging
from bollydog.models.service import AppService
from .mock import SimExchange

log = logging.getLogger(__name__)


class ExchangeService(AppService):
    domain = "exchange"
    alias = "ExchangeService"
    commands = []

    def __init__(self, initial_balance: float = 100_000.0, slippage_pct: float = 0.001,
                 commission_rate: float = 0.001, **kwargs):
        self._initial_balance = initial_balance
        self._slippage_pct = slippage_pct
        self._commission_rate = commission_rate
        self.exchange: SimExchange = None
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self.exchange = SimExchange(initial_balance=self._initial_balance,
                                   slippage_pct=self._slippage_pct,
                                   commission_rate=self._commission_rate)
        log.info(f'[交易所] 就绪 balance={self._initial_balance} slip={self._slippage_pct} fee={self._commission_rate}')
        await super().on_start()

    def submit_order(self, order: dict, bar: dict) -> dict | None:
        return self.exchange.submit_order(order, bar)

    def check_pending(self, bar: dict) -> list[dict]:
        return self.exchange.check_pending(bar)

    def cancel_order(self, order_id: str) -> bool:
        return self.exchange.cancel_order(order_id)

    def get_account(self) -> dict:
        return {"initial_balance": self.exchange.initial_balance, "total": self.exchange.total,
                "net_pnl": self.exchange.total - self.exchange.initial_balance}

    def reset(self):
        self.exchange.reset()
