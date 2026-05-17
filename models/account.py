"""Account — 账户事实表模型，记录在 SimExchange 中。"""
import time
from pydantic import BaseModel


class Account(BaseModel):
    model_config = {"frozen": False}
    account_id: str = "default"
    currency: str = "CNY"
    initial_balance: float = 0.0
    total: float = 0.0
    total_commission: float = 0.0
    total_realized_pnl: float = 0.0
    trade_count: int = 0
    updated_at: int = 0

    @property
    def free(self) -> float: return self.total
    @property
    def net_pnl(self) -> float: return self.total - self.initial_balance

    def settle(self, pnl: float, commission: float):
        """结算一笔成交：加盈亏、扣手续费、更新统计。"""
        self.total += pnl - commission
        self.total_commission += commission
        self.total_realized_pnl += pnl
        self.trade_count += 1
        self.updated_at = int(time.time() * 1000)
