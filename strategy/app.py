"""FibStrategy — 斐波那契策略服务。订阅 SignalEmitted → 决定是否下单。"""
import os, logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.adapters.duckdb import TimingDuckDBProtocol
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
        self.db: TimingDuckDBProtocol = None
        self.run_id: str = ""
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self.db = TimingDuckDBProtocol.shared()
        if not self.db.adapter:
            await self.db.on_start()
        self.run_id = os.environ.get("TIMING_RUN_ID", "live_default")
        log.info(f'[FibStrategy] DB就绪, run_id={self.run_id}')
        await super().on_start()

    async def on_signal(self, cmd):
        symbol = getattr(cmd, 'symbol', '') or ''
        direction = getattr(cmd, 'direction', 'neutral') or 'neutral'
        strength = getattr(cmd, 'strength', 0) or 0
        price = getattr(cmd, 'price', 0) or 0
        ts = getattr(cmd, 'ts', 0) or 0
        if not symbol:
            return

        if direction == "neutral" or strength < self.min_strength:
            reason = "neutral" if direction == "neutral" else f"strength({strength:.2f})<min({self.min_strength})"
            await self._record_decision(symbol, ts, direction, strength, price, "skip", reason)
            return

        side = "buy" if direction == "long" else "sell"
        bar = {"close": price, "ts": ts}
        qty = self.position_size
        log.info(f'[策略] {direction} {symbol} 强度={strength:.2f} → {side} qty={qty}')
        await self._record_decision(symbol, ts, direction, strength, price, "submit", f"{side} qty={qty}")
        await hub.execute(SubmitOrder(symbol=symbol, side=side, quantity=qty, bar=bar))

    async def _record_decision(self, symbol: str, ts: int, direction: str, strength: float, price: float, action: str, reason: str):
        if not self.db:
            return
        await self.db.append("decisions", {"run_id": self.run_id, "symbol": symbol, "ts": ts,
                                           "direction": direction, "strength": strength,
                                           "price": price, "action": action, "reason": reason})

    async def on_stop(self):
        await super().on_stop()
