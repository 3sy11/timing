"""FibStrategy — 斐波那契策略服务。订阅 SignalEmitted → 决定是否下单。"""
import os, logging
from bollydog.models.service import AppService
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.globals import hub
from timing.execution.models import SubmitOrder
from timing.strategy.models import StrategyDecision

log = logging.getLogger(__name__)
DEFAULT_POSITION_SIZE = 0.1
MIN_SIGNAL_STRENGTH = 0.6


class FibStrategy(AppService):
    domain = "strategy"
    alias = "FibStrategy"
    commands = ["models"]

    def __init__(self, position_size: float = DEFAULT_POSITION_SIZE,
                 min_strength: float = MIN_SIGNAL_STRENGTH, cache_path: str = "cache/strategy", **kwargs):
        self.position_size = position_size
        self.min_strength = min_strength
        self._cache_path = cache_path
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        if not self.protocol:
            os.makedirs(self._cache_path, exist_ok=True)
            db_path = os.path.join(self._cache_path, "fib_strategy.sqlite")
            inner = SQLiteProtocol(path=db_path)
            cache = CacheLayer(flush_threshold=1)
            cache.add_dependency(inner)
            self.protocol = cache
            await cache.maybe_start()
            log.info(f'[FibStrategy] 协议链就绪: CacheLayer → SQLite({db_path})')
        await super().on_start()

    async def on_signal(self, cmd):
        symbol = getattr(cmd, 'symbol', '') or ''
        direction = getattr(cmd, 'direction', 'neutral') or 'neutral'
        strength = getattr(cmd, 'strength', 0) or 0
        price = getattr(cmd, 'price', 0) or 0
        ts = getattr(cmd, 'ts', 0) or 0
        if not symbol: return

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
        if not self.protocol: return
        decision = StrategyDecision(ts=ts, symbol=symbol, direction=direction, strength=strength, price=price, action=action, reason=reason)
        key = f"decisions:{symbol}"
        existing = await self.protocol.get(key) or []
        existing.append(decision.model_dump())
        await self.protocol.set(key, existing)
