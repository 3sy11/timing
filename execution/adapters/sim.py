"""SimExchange — 模拟交易所纯撮合引擎。

不再在协议链中，只负责撮合逻辑。
Market 单按 close ± slippage 立即成交；Limit/Stop 挂单后续 bar 检查触发。
"""
import logging
from timing.models.order import Order, FillResult
from timing.models.account import Account

log = logging.getLogger(__name__)


class SimExchange:
    def __init__(self, initial_balance: float = 100_000.0, slippage_pct: float = 0.001,
                 commission_rate: float = 0.001):
        self._initial_balance = initial_balance
        self.slippage_pct = slippage_pct
        self.commission_rate = commission_rate
        self.account = Account(initial_balance=initial_balance, total=initial_balance)
        self._pending_orders: list[Order] = []

    def reset(self):
        self.account = Account(initial_balance=self._initial_balance, total=self._initial_balance)
        self._pending_orders = []

    def submit_order(self, order: Order, bar: dict = None) -> FillResult | None:
        if order.order_type == "market":
            return self._fill_market(order, bar)
        self._pending_orders.append(order)
        order.status = "submitted"
        log.info(f'[SimExchange] 挂单 {order.order_type} {order.side} {order.symbol} qty={order.quantity} px={order.price}')
        return None

    def check_pending(self, bar: dict) -> list[FillResult]:
        fills, remaining = [], []
        for order in self._pending_orders:
            triggered = False
            if order.order_type == "limit":
                triggered = (order.side == "buy" and bar["low"] <= order.price) or \
                            (order.side == "sell" and bar["high"] >= order.price)
            elif order.order_type == "stop":
                triggered = (order.side == "buy" and bar["high"] >= order.stop_price) or \
                            (order.side == "sell" and bar["low"] <= order.stop_price)
            if triggered:
                fills.append(self._fill_market(order, bar))
            else:
                remaining.append(order)
        self._pending_orders = remaining
        return fills

    def cancel_order(self, order_id: str) -> bool:
        for i, o in enumerate(self._pending_orders):
            if o.order_id == order_id:
                self._pending_orders.pop(i)
                o.status = "canceled"
                log.info(f'[SimExchange] 撤单 {order_id}')
                return True
        return False

    def _fill_market(self, order: Order, bar: dict) -> FillResult:
        base_price = bar.get("close", bar.get("open", 0.0))
        slip_direction = 1 if order.side == "buy" else -1
        fill_price = base_price * (1 + self.slippage_pct * slip_direction)
        commission = fill_price * order.quantity * self.commission_rate
        cost = fill_price * order.quantity
        pnl = -cost if order.side == "buy" else cost
        self.account.settle(pnl, commission)
        order.mark_filled(fill_price, order.quantity, commission, bar.get("ts", 0))
        log.info(f'[SimExchange] {order.side} {order.symbol} qty={order.quantity} px={fill_price:.4f} fee={commission:.4f}')
        return FillResult(order_id=order.order_id, symbol=order.symbol, side=order.side,
                          filled_price=fill_price, filled_quantity=order.quantity,
                          commission=commission, ts=bar.get("ts", 0))
