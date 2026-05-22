"""Broker — 订单管理 + 持仓追踪。protocol 链: SimExchangeProtocol → CacheLayer → SQLiteProtocol。"""
import logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.models.order import Order, FillResult, OrderFilled, OrderRejected
from timing.models.position import Position
from timing.models.account import Account

log = logging.getLogger(__name__)


class Broker(AppService):
    domain = "execution"
    alias = "Broker"
    commands = ["models"]
    router_mapping = {"SubmitOrder": ["POST", "/api/timing/submit_order"]}

    def __init__(self, **kwargs):
        self._positions: dict[str, Position] = {}
        super().__init__(**kwargs)

    async def on_started(self):
        pos_data = await self.protocol.get("__positions") or {}
        for sym, pdata in pos_data.items():
            self._positions[sym] = Position(**pdata)
        log.info(f'[Broker] 就绪 持仓数={len(self._positions)}')
        await super().on_started()

    async def on_submit_order(self, symbol: str, side: str, order_type: str,
                              quantity: float, price: float, stop_price: float, bar: dict) -> dict | None:
        order = Order(symbol=symbol, side=side, order_type=order_type, quantity=quantity,
                      price=price, stop_price=stop_price, created_at=bar.get("ts", 0))
        await self._persist_order(order)

        exchange = self.protocol
        fill = await exchange.submit_order(order, bar)
        if fill:
            await self._process_fill(fill)
            order.mark_filled(fill.filled_price, fill.filled_quantity, fill.commission, fill.ts)
            await self._persist_order(order)
            return fill.model_dump()
        if order.status == "rejected":
            await self._persist_order(order)
            await self._sync_emit(OrderRejected(order_id=order.order_id, symbol=symbol, reason="exchange_rejected", ts=bar.get("ts", 0)))
            return None
        await self._persist_order(order)
        return None

    async def process_pending(self, bar: dict) -> list[dict]:
        exchange = self.protocol
        fills = exchange.check_pending(bar)
        results = []
        for fill in fills:
            await self._process_fill(fill)
            results.append(fill.model_dump())
        return results

    async def _process_fill(self, fill: FillResult):
        pos = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        rpnl = pos.apply_fill(fill)
        self._positions[fill.symbol] = pos
        account = await self.protocol.get_balance()
        if rpnl != 0: account.settle(rpnl, 0)
        await self._persist_positions()
        await self.protocol.set(f"__fills:{fill.order_id}", fill.model_dump())
        await self._sync_emit(OrderFilled(order_id=fill.order_id, symbol=fill.symbol, side=fill.side,
                                         filled_price=fill.filled_price, filled_quantity=fill.filled_quantity,
                                         commission=fill.commission, realized_pnl=rpnl, ts=fill.ts))
        log.info(f'[Broker] 成交 {fill.side} {fill.symbol} qty={fill.filled_quantity} rpnl={rpnl:.4f}')

    async def _sync_emit(self, event):
        await hub.dispatch(event)

    async def _persist_order(self, order: Order):
        await self.protocol.set(f"__orders:{order.order_id}", order.model_dump())

    async def _persist_positions(self):
        await self.protocol.set("__positions", {s: p.model_dump() for s, p in self._positions.items()})

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def get_all_positions(self) -> dict[str, Position]:
        return self._positions

    async def get_account(self) -> Account:
        return await self.protocol.get_balance()

    async def on_cancel_order(self, order_id: str) -> dict:
        success = await self.protocol.cancel_order(order_id)
        return {"order_id": order_id, "canceled": success}
