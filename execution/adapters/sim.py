"""
SimExchangeProtocol — 模拟交易所撮合引擎。

【撮合规则】
  Market 单：按当根 bar 的 close 价 ± slippage 立即全额成交
  Limit 单：挂单暂存，后续 bar 的 low/high 触达价格时成交
  Stop 单：挂单暂存，后续 bar 的 high/low 触达止损价时转为 Market 成交

【资金管理】
  内置一个 Account 对象跟踪余额变动（买入扣钱，卖出加钱，扣手续费）

【在 protocol 链中的位置】
  Broker.protocol → SimExchangeProtocol → CacheLayer → SQLiteProtocol
  撮合操作由本类处理，KV 读写委托给内层 CacheLayer
"""
import logging
from timing.execution.adapters.base import ExchangeProtocol
from timing.models.order import Order, FillResult
from timing.models.account import Account

log = logging.getLogger(__name__)


class SimExchangeProtocol(ExchangeProtocol):

    def __init__(self, initial_balance: float = 100_000.0, slippage_pct: float = 0.001,
                 commission_rate: float = 0.001, **kwargs):
        self._initial_balance = initial_balance
        self.slippage_pct = slippage_pct
        self.commission_rate = commission_rate
        self.account: Account = None
        self._pending_orders: list[Order] = []
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        """初始化账户和挂单列表。"""
        self.account = Account(initial_balance=self._initial_balance, total=self._initial_balance, locked=0.0)
        self._pending_orders = []
        self.adapter = {"account": self.account}
        log.info(f'[模拟交易所] 就绪 初始资金={self._initial_balance} 滑点={self.slippage_pct} 手续费率={self.commission_rate}')

    # ──────────────── 下单 ────────────────

    async def submit_order(self, order: Order, bar: dict = None) -> FillResult:
        """Market 单立即撮合；Limit/Stop 单放入挂单队列。"""
        if order.order_type == "market":
            return self._fill_market(order, bar)
        self._pending_orders.append(order)
        order.status = "submitted"
        log.info(f'[模拟交易所] 挂单 {order.order_type} {order.side} {order.symbol} 数量={order.quantity} 价格={order.price}')
        return None

    def _fill_market(self, order: Order, bar: dict) -> FillResult:
        """Market 撮合：close ± 滑点 → 扣钱/加钱 → 返回成交结果。"""
        base_price = bar.get("close", bar.get("open", 0.0))
        # 买入时加滑点（成交价更高），卖出时减滑点（成交价更低）
        slip_direction = 1 if order.side == "buy" else -1
        fill_price = base_price * (1 + self.slippage_pct * slip_direction)
        commission = fill_price * order.quantity * self.commission_rate
        cost = fill_price * order.quantity
        # 更新账户余额
        if order.side == "buy":
            self.account.total -= cost + commission
        else:
            self.account.total += cost - commission
        order.mark_filled(fill_price, order.quantity, commission, bar.get("ts", 0))
        log.info(f'[模拟交易所] 成交 {order.side} {order.symbol} 数量={order.quantity} 价格={fill_price:.4f} 手续费={commission:.4f}')
        return FillResult(order_id=order.order_id, symbol=order.symbol, side=order.side,
                          filled_price=fill_price, filled_quantity=order.quantity, commission=commission, ts=bar.get("ts", 0))

    # ──────────────── 挂单检查 ────────────────

    def check_pending(self, bar: dict) -> list[FillResult]:
        """每根新 bar 调用：检查挂单是否触发。"""
        fills, remaining = [], []
        for order in self._pending_orders:
            triggered = False
            if order.order_type == "limit":
                # Limit Buy：bar 最低价 ≤ 限价 → 触发
                # Limit Sell：bar 最高价 ≥ 限价 → 触发
                triggered = (order.side == "buy" and bar["low"] <= order.price) or \
                            (order.side == "sell" and bar["high"] >= order.price)
            elif order.order_type == "stop":
                # Stop Buy：bar 最高价 ≥ 止损价 → 触发
                # Stop Sell：bar 最低价 ≤ 止损价 → 触发
                triggered = (order.side == "buy" and bar["high"] >= order.stop_price) or \
                            (order.side == "sell" and bar["low"] <= order.stop_price)
            if triggered:
                fill = self._fill_market(order, bar)
                fills.append(fill)
            else:
                remaining.append(order)
        self._pending_orders = remaining
        return fills

    # ──────────────── 撤单 / 查余额 ────────────────

    async def cancel_order(self, order_id: str) -> bool:
        for i, o in enumerate(self._pending_orders):
            if o.order_id == order_id:
                self._pending_orders.pop(i)
                o.status = "canceled"
                log.info(f'[模拟交易所] 撤单 {order_id}')
                return True
        return False

    async def get_balance(self) -> Account:
        return self.account
