"""Event 消息类 — 用于 Hub 事件分发，不入库。"""
from typing import ClassVar
from pydantic import Field
from bollydog.models.base import BaseEvent


class SignalEmitted(BaseEvent):
    destination: ClassVar[str] = "analysis.AnalysisEngine.SignalEmitted"
    ts: int = 0
    symbol: str = ""
    interval: str = ""
    direction: str = "neutral"
    strength: float = 0.0
    source: str = ""
    price: float = 0.0
    level: float = None
    metadata: dict = Field(default_factory=dict)


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
