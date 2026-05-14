"""
Broker — 执行层核心服务，负责下单和持仓管理。

【职责】
  接收 SubmitOrder 命令 → 检查余额 → 交给交易所撮合 → 更新持仓 → 广播成交事件

【protocol 链（TOML 配置）】
  SimExchangeProtocol（撮合逻辑）
    → CacheLayer（内存缓存持仓等状态）
      → SQLiteProtocol（落盘持久化）

  self.protocol 指向最外层的 SimExchangeProtocol，它同时提供：
  - submit_order / cancel_order / get_balance → 交易所操作
  - get / set / remove → 委托给内层 CacheLayer→SQLite 做 KV 持久化
"""
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

    def __init__(self, **kwargs):
        self._positions: dict[str, Position] = {}
        super().__init__(**kwargs)

    async def on_started(self) -> None:
        """启动完成后：从持久化恢复上次的持仓数据，打印账户余额。"""
        saved = await self.protocol.get("__positions") or {}
        for symbol, data in saved.items():
            self._positions[symbol] = Position(**data) if isinstance(data, dict) else data
        account = await self.protocol.get_balance()
        log.info(f'[券商] 就绪 余额={account.total:.2f} 持仓数={len(self._positions)}')
        await super().on_started()

    async def on_submit_order(self, order: Order, bar: dict = None) -> FillResult:
        """
        处理下单请求：
        1. 检查余额是否足够
        2. 调用交易所协议撮合
        3. 更新本地持仓并持久化
        4. 广播成交/拒绝事件
        """
        # ① 余额检查
        account = await self.protocol.get_balance()
        cost = order.price * order.quantity if order.order_type != "market" else (bar or {}).get("close", 0) * order.quantity
        if order.side == "buy" and account.free < cost:
            log.warning(f'[券商] 拒绝下单 {order.order_id}: 余额 {account.free:.2f} < 需要 {cost:.2f}')
            await hub.emit(OrderRejected(order_id=order.order_id, symbol=order.symbol, reason=f"余额不足"))
            return None

        # ② 交给交易所撮合
        fill = await self.protocol.submit_order(order, bar)
        if fill is None: return None

        # ③ 更新持仓
        pos = self._positions.get(order.symbol) or Position(symbol=order.symbol)
        rpnl = pos.apply_fill(fill)
        self._positions[order.symbol] = pos

        # ④ 持久化
        await self.protocol.set("__positions", {s: p.model_dump() for s, p in self._positions.items()})

        log.info(f'[券商] 成交 {fill.side} {fill.symbol} 数量={fill.filled_quantity} 价格={fill.filled_price:.4f} 盈亏={rpnl:.4f}')

        # ⑤ 广播成交事件（下游可订阅做风控/记录）
        await hub.emit(OrderFilled(
            order_id=fill.order_id, symbol=fill.symbol, side=fill.side,
            filled_price=fill.filled_price, filled_quantity=fill.filled_quantity,
            commission=fill.commission, realized_pnl=rpnl, ts=fill.ts))
        return fill

    def get_position(self, symbol: str) -> Position:
        return self._positions.get(symbol) or Position(symbol=symbol)

    def get_all_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    async def get_account(self) -> Account:
        return await self.protocol.get_balance()
