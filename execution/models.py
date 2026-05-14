"""
执行层命令 — SubmitOrder / CancelOrder。

【调用方式】
  策略层构造 SubmitOrder 命令 → hub.execute(order)
  框架根据 destination 找到 Broker → 进入 Broker 的上下文 → 执行 __call__

【destination 路由】
  "execution.Broker.SubmitOrder" → 框架定位到 Broker 服务
  __call__ 中的 `app` 就是当前上下文的 Broker 实例
"""
from typing import ClassVar, Literal
from pydantic import Field
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.models.order import Order


class SubmitOrder(BaseCommand):
    """下单命令 — 策略层 → Broker。"""
    destination: ClassVar[str] = "execution.Broker.SubmitOrder"
    symbol: str = ""
    side: Literal["buy", "sell"] = "buy"
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    bar: dict = Field(default_factory=dict)

    async def __call__(self):
        # 构造 Order 对象，交给 Broker.on_submit_order 处理
        order = Order(symbol=self.symbol, side=self.side, order_type=self.order_type,
                      quantity=self.quantity, price=self.price, stop_price=self.stop_price,
                      created_at=self.bar.get("ts", 0))
        result = await app.on_submit_order(order, bar=self.bar)
        return result


class CancelOrder(BaseCommand):
    """撤单命令。"""
    destination: ClassVar[str] = "execution.Broker.CancelOrder"
    order_id: str = ""

    async def __call__(self):
        result = await app.protocol.cancel_order(self.order_id)
        return result
