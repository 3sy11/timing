"""Account — 账户事实表模型，记录在 SimExchange 中，含余额管理 + 台账记录。"""
import time
from typing import Literal
from pydantic import BaseModel, Field


class Account(BaseModel):
    model_config = {"frozen": False}
    account_id: str = "default"
    currency: str = "CNY"
    initial_balance: float = 0.0
    total: float = 0.0
    locked: float = 0.0
    total_commission: float = 0.0
    total_realized_pnl: float = 0.0
    trade_count: int = 0
    updated_at: int = 0

    @property
    def free(self) -> float: return self.total - self.locked
    @property
    def net_pnl(self) -> float: return self.total - self.initial_balance

    def lock(self, amount: float):
        if amount > self.free: raise ValueError(f"余额不足: {self.free} < {amount}")
        self.locked += amount

    def unlock(self, amount: float):
        self.locked = max(0.0, self.locked - amount)

    def settle(self, pnl: float, commission: float):
        """结算一笔成交：加盈亏、扣手续费、更新统计。"""
        self.total += pnl - commission
        self.total_commission += commission
        self.total_realized_pnl += pnl
        self.trade_count += 1
        self.updated_at = int(time.time() * 1000)


class LedgerEntry(BaseModel):
    model_config = {"frozen": True}
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))
    entry_type: Literal["commission", "realized_pnl", "deposit", "withdrawal", "lock", "unlock"] = "commission"
    amount: float = 0.0
    balance_after: float = 0.0
    order_id: str = ""
    symbol: str = ""
    memo: str = ""
