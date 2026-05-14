"""
FibStrategy — 基于斐波那契回撤的交易策略。

【职责】
  订阅分析层产出的 SignalEmitted 事件 → 根据方向和强度决定是否下单 → 发给 Broker 执行。

【调用链路】
  AnalysisEngine.on_bar 产出信号
    → hub.execute(subscriber cmd) → FibStrategy.on_signal
      → hub.execute(SubmitOrder) → Broker.on_submit_order → SimExchange 撮合

【配置项（config.toml）】
  position_size = 0.1   # 每次下单数量
  min_strength  = 0.6   # 信号强度阈值，低于此值不下单
"""
import logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.models.signal import SignalEmitted
from timing.execution.models import SubmitOrder

log = logging.getLogger(__name__)

DEFAULT_POSITION_SIZE = 0.1
MIN_SIGNAL_STRENGTH = 0.6


class FibStrategy(AppService):
    domain = "strategy"
    alias = "FibStrategy"
    commands = ["models"]

    def __init__(self, position_size: float = DEFAULT_POSITION_SIZE,
                 min_strength: float = MIN_SIGNAL_STRENGTH, **kwargs):
        self.position_size = position_size
        self.min_strength = min_strength
        super().__init__(**kwargs)

    async def on_signal(self, cmd):
        """
        收到分析层的 SignalEmitted 信号后：
        1. 解析方向和强度
        2. 过滤弱信号（strength < min_strength）
        3. 构造 SubmitOrder 命令发给 Broker
        """
        event_data = cmd.get_event()
        if not event_data: return

        symbol = event_data.get("symbol", "")
        direction = event_data.get("direction", "neutral")
        strength = event_data.get("strength", 0)
        price = event_data.get("price", 0)

        # 过滤：方向不明 或 强度不够 → 不操作
        if direction == "neutral" or strength < self.min_strength:
            return

        side = "buy" if direction == "long" else "sell"
        bar = {"close": price, "ts": event_data.get("ts", 0)}
        log.info(f'[策略] 收到信号 {direction} {symbol} 强度={strength:.2f} → 下单 {side} 数量={self.position_size}')

        # 同步执行下单（hub.execute 保证回测中等到 Broker 处理完）
        order = SubmitOrder(symbol=symbol, side=side, quantity=self.quantity_for(price), bar=bar)
        await hub.execute(order)

    def quantity_for(self, price: float) -> float:
        """计算下单数量（当前简单返回固定值，后续可扩展为按资金比例）"""
        return self.position_size
