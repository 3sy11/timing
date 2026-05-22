"""Order + FillResult + OrderFilled/OrderRejected 事件。"""
import uuid
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from bollydog.models.base import BaseEvent


class Order(BaseModel):
    model_config = {"frozen": False}
    order_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    symbol: str = ""
    side: Literal["buy", "sell"] = "buy"
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    status: Literal["pending", "submitted", "filled", "partially_filled", "canceled", "rejected"] = "pending"
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    commission: float = 0.0
    created_at: int = 0
    updated_at: int = 0

    def mark_filled(self, price: float, qty: float, commission: float, ts: int):
        self.status = "filled"
        self.filled_price = price
        self.filled_quantity = qty
        self.commission = commission
        self.updated_at = ts


class FillResult(BaseModel):
    model_config = {"frozen": True}
    order_id: str = ""
    symbol: str = ""
    side: str = "buy"
    filled_price: float = 0.0
    filled_quantity: float = 0.0
    commission: float = 0.0
    ts: int = 0


class OrderFilled(BaseEvent):
    destination: ClassVar[str] = "execution.Broker.OrderFilled"
    order_id: str = ""
    symbol: str = ""
    side: str = "buy"
    filled_price: float = 0.0
    filled_quantity: float = 0.0
    commission: float = 0.0
    realized_pnl: float = 0.0
    ts: int = 0


class OrderRejected(BaseEvent):
    destination: ClassVar[str] = "execution.Broker.OrderRejected"
    order_id: str = ""
    symbol: str = ""
    reason: str = ""
    ts: int = 0
