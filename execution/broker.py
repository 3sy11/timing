"""Broker — 订单管理 + 持仓追踪。直连 SQLite，无缓存层。"""
import os, logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.adapters.sqlite import TableSchema, StructuredSQLiteProtocol
from timing.models.order import Order, FillResult, OrderFilled, OrderRejected
from timing.models.position import Position
from timing.models.account import Account
from timing.execution.adapters.sim import SimExchange

log = logging.getLogger(__name__)
DATA_ROOT = os.environ.get("TIMING_DATA_ROOT", "warehouse/timing")

BROKER_SCHEMAS = [
    TableSchema(model=Position, table="positions", key_columns=["symbol"]),
    TableSchema(model=Order, table="orders", key_columns=["order_id"]),
    TableSchema(model=FillResult, table="fills", key_columns=["order_id"]),
]


class Broker(AppService):
    domain = "execution"
    alias = "Broker"
    commands = ["models"]
    router_mapping = {"SubmitOrder": ["POST", "/api/timing/submit_order"]}

    def __init__(self, initial_balance: float = 100000, slippage_pct: float = 0.001,
                 commission_rate: float = 0.001, cache_path: str = None, **kwargs):
        self._initial_balance = initial_balance
        self._slippage_pct = slippage_pct
        self._commission_rate = commission_rate
        self._cache_path = cache_path or DATA_ROOT
        self.db: StructuredSQLiteProtocol = None
        self.exchange: SimExchange = None
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        os.makedirs(self._cache_path, exist_ok=True)
        db_path = os.path.join(self._cache_path, "execution_broker.sqlite")
        self.db = StructuredSQLiteProtocol(path=db_path, schemas=BROKER_SCHEMAS)
        await self.db.on_start()
        self.exchange = SimExchange(initial_balance=self._initial_balance,
                                   slippage_pct=self._slippage_pct, commission_rate=self._commission_rate)
        log.info(f'[Broker] DB就绪: {db_path}')
        await super().on_start()

    async def on_started(self):
        positions = await self.db.all("positions")
        log.info(f'[Broker] 就绪 持仓数={len(positions)}')
        await super().on_started()

    async def on_submit_order(self, symbol: str, side: str, order_type: str,
                              quantity: float, price: float, stop_price: float, bar: dict) -> dict | None:
        order = Order(symbol=symbol, side=side, order_type=order_type, quantity=quantity,
                      price=price, stop_price=stop_price, created_at=bar.get("ts", 0))
        await self.db.put("orders", order.model_dump(), order_id=order.order_id)
        fill = self.exchange.submit_order(order, bar)
        if fill:
            await self._process_fill(fill)
            order.mark_filled(fill.filled_price, fill.filled_quantity, fill.commission, fill.ts)
            await self.db.put("orders", order.model_dump(), order_id=order.order_id)
            return fill.model_dump()
        if order.status == "rejected":
            await self.db.put("orders", order.model_dump(), order_id=order.order_id)
            await self._sync_emit(OrderRejected(order_id=order.order_id, symbol=symbol, reason="exchange_rejected", ts=bar.get("ts", 0)))
            return None
        await self.db.put("orders", order.model_dump(), order_id=order.order_id)
        return None

    async def process_pending(self, bar: dict) -> list[dict]:
        fills = self.exchange.check_pending(bar)
        results = []
        for fill in fills:
            await self._process_fill(fill)
            results.append(fill.model_dump())
        return results

    async def _process_fill(self, fill: FillResult):
        pos_data = await self.db.get("positions", symbol=fill.symbol)
        pos = Position(**(pos_data or {"symbol": fill.symbol}))
        rpnl = pos.apply_fill(fill)
        await self.db.put("positions", pos.model_dump(), symbol=fill.symbol)
        await self.db.put("fills", fill.model_dump(), order_id=fill.order_id)
        if rpnl != 0: self.exchange.account.settle(rpnl, 0)
        await self._sync_emit(OrderFilled(order_id=fill.order_id, symbol=fill.symbol, side=fill.side,
                                         filled_price=fill.filled_price, filled_quantity=fill.filled_quantity,
                                         commission=fill.commission, realized_pnl=rpnl, ts=fill.ts))
        log.info(f'[Broker] 成交 {fill.side} {fill.symbol} qty={fill.filled_quantity} rpnl={rpnl:.4f}')

    async def _sync_emit(self, event):
        await hub.dispatch(event)

    async def get_position(self, symbol: str) -> Position | None:
        data = await self.db.get("positions", symbol=symbol)
        return Position(**data) if data else None

    async def get_all_positions(self) -> dict[str, Position]:
        rows = await self.db.all("positions")
        return {r["symbol"]: Position(**r) for r in rows}

    async def get_account(self) -> Account:
        return self.exchange.account

    async def on_cancel_order(self, order_id: str) -> dict:
        success = self.exchange.cancel_order(order_id)
        return {"order_id": order_id, "canceled": success}

    async def on_stop(self):
        if self.db: await self.db.on_stop()
        await super().on_stop()
