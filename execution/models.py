"""Execution Command 定义 — SubmitOrder / CancelOrder。"""
from typing import Any, ClassVar
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand


class SubmitOrder(BaseCommand):
    destination: ClassVar[str] = "execution.Broker.SubmitOrder"
    symbol: str = ""
    side: str = "buy"
    order_type: str = "market"
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    bar: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        return await app.on_submit_order(self.symbol, self.side, self.order_type,
                                         self.quantity, self.price, self.stop_price, self.bar)


class CancelOrder(BaseCommand):
    destination: ClassVar[str] = "execution.Broker.CancelOrder"
    order_id: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        return await app.on_cancel_order(self.order_id)
