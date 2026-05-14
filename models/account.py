"""Account — 账户余额 + LedgerEntry 台账记录。"""
import time
from typing import Literal, List
from pydantic import BaseModel, Field


class Account(BaseModel):
    model_config = {"frozen": False}
    total: float = 0.0
    locked: float = 0.0

    @property
    def free(self) -> float: return self.total - self.locked

    def lock(self, amount: float):
        if amount > self.free: raise ValueError(f"insufficient free balance: {self.free} < {amount}")
        self.locked += amount

    def unlock(self, amount: float):
        self.locked = max(0.0, self.locked - amount)

    def settle(self, cost: float, pnl: float, commission: float):
        """结算一笔成交：解锁预留资金，扣成本、加盈亏、扣手续费。"""
        self.unlock(cost)
        self.total += pnl - commission


class LedgerEntry(BaseModel):
    model_config = {"frozen": True}
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))
    entry_type: Literal["commission", "realized_pnl", "deposit", "withdrawal", "lock", "unlock"] = "commission"
    amount: float = 0.0
    balance_after: float = 0.0
    order_id: str = ""
    symbol: str = ""
    memo: str = ""
