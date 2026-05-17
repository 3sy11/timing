"""Broker — 执行层核心服务，负责下单和持仓管理。
protocol 链：SimExchangeProtocol → CacheLayer → SQLiteProtocol
"""
import logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.models.order import Order, FillResult, OrderFilled, OrderRejected
from timing.models.position import Position

log = logging.getLogger(__name__)


class Broker(AppService):
    domain = "execution"
    alias = "Broker"
    commands = ["models"]

    def __init__(self, **kwargs):
        self._positions: dict[str, Position] = {}
        super().__init__(**kwargs)

    async def on_started(self) -> None:
        saved = await self.protocol.get("__positions") or {}
        for symbol, data in saved.items():
            self._positions[symbol] = Position(**data) if isinstance(data, dict) else data
        account = await self.protocol.get_balance()
        log.info(f'[Broker] 就绪 余额={account.total:.2f} 持仓数={len(self._positions)}')
        await super().on_started()

    async def on_submit_order(self, order: Order, bar: dict = None) -> FillResult:
        account = await self.protocol.get_balance()
        cost = order.price * order.quantity if order.order_type != "market" else (bar or {}).get("close", 0) * order.quantity
        if order.side == "buy" and account.free < cost:
            log.warning(f'[Broker] 拒绝 {order.order_id}: 余额 {account.free:.2f} < {cost:.2f}')
            await self._sync_emit(OrderRejected(order_id=order.order_id, symbol=order.symbol, reason="余额不足"))
            return None
        fill = await self.protocol.submit_order(order, bar)
        if fill is None: return None
        return await self._process_fill(fill)

    async def _process_fill(self, fill: FillResult) -> FillResult:
        """更新持仓 + 持久化 + 同步广播成交事件。"""
        pos = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        rpnl = pos.apply_fill(fill)
        self._positions[fill.symbol] = pos
        await self.protocol.set("__positions", {s: p.model_dump() for s, p in self._positions.items()})
        log.info(f'[Broker] 成交 {fill.side} {fill.symbol} qty={fill.filled_quantity} px={fill.filled_price:.4f} rpnl={rpnl:.4f}')
        await self._sync_emit(OrderFilled(
            order_id=fill.order_id, symbol=fill.symbol, side=fill.side,
            filled_price=fill.filled_price, filled_quantity=fill.filled_quantity,
            commission=fill.commission, realized_pnl=rpnl, ts=fill.ts))
        return fill

    async def process_pending(self, bar: dict) -> list[FillResult]:
        """检查挂单触发，逐个走完整成交流程。"""
        fills = self.protocol.check_pending(bar)
        return [await self._process_fill(f) for f in fills]

    @staticmethod
    async def _sync_emit(event):
        """同步广播事件：exchange.match + hub.execute，保证回测链路完整。"""
        for handler_cls in hub.exchange.match(type(event).destination):
            cmd = handler_cls()
            cmd.add_event(event)
            await hub.execute(cmd)

    def get_position(self, symbol: str) -> Position:
        return self._positions.get(symbol) or Position(symbol=symbol)

    def get_all_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    async def get_account(self):
        return await self.protocol.get_balance()
