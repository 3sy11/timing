"""Position — 持仓事实表（5 字段）。"""
from typing import Literal
from pydantic import BaseModel
from timing.models.order import FillResult


class Position(BaseModel):
    model_config = {"frozen": False}
    symbol: str = ""
    side: Literal["long", "short", "flat"] = "flat"
    quantity: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0

    def apply_fill(self, fill: FillResult) -> float:
        rpnl = 0.0
        if self.side == "flat":
            self.side = "long" if fill.side == "buy" else "short"
            self.quantity = fill.filled_quantity
            self.avg_entry_price = fill.filled_price
        elif (self.side == "long" and fill.side == "buy") or (self.side == "short" and fill.side == "sell"):
            total_cost = self.avg_entry_price * self.quantity + fill.filled_price * fill.filled_quantity
            self.quantity += fill.filled_quantity
            self.avg_entry_price = total_cost / self.quantity if self.quantity else 0.0
        else:
            if fill.filled_quantity >= self.quantity:
                direction = 1 if self.side == "long" else -1
                rpnl = direction * (fill.filled_price - self.avg_entry_price) * self.quantity
                remaining = fill.filled_quantity - self.quantity
                if remaining > 0:
                    self.side = "long" if fill.side == "buy" else "short"
                    self.quantity = remaining
                    self.avg_entry_price = fill.filled_price
                else:
                    self.side = "flat"
                    self.quantity = 0.0
                    self.avg_entry_price = 0.0
            else:
                direction = 1 if self.side == "long" else -1
                rpnl = direction * (fill.filled_price - self.avg_entry_price) * fill.filled_quantity
                self.quantity -= fill.filled_quantity
        self.realized_pnl += rpnl
        return rpnl
