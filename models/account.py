"""Account — 账户事实表（2 字段）。"""
from pydantic import BaseModel


class Account(BaseModel):
    model_config = {"frozen": False}
    initial_balance: float = 0.0
    total: float = 0.0

    @property
    def free(self) -> float: return self.total
    @property
    def net_pnl(self) -> float: return self.total - self.initial_balance

    def settle(self, pnl: float, commission: float):
        self.total += pnl - commission
