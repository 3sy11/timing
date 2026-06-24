"""Broker — 订单管理 + 持仓追踪。使用共享 DuckDBProtocol。"""
import os, uuid, logging
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.adapters.duckdb import TimingDuckDBProtocol
from timing.models.events import OrderFilled, OrderRejected
from timing.models.exchange import SimExchange

log = logging.getLogger(__name__)


class Broker(AppService):
    domain = "execution"
    alias = "Broker"
    commands = ["models"]
    router_mapping = {"SubmitOrder": ["POST", "/api/timing/submit_order"]}

    def __init__(self, initial_balance: float = 100000, slippage_pct: float = 0.001,
                 commission_rate: float = 0.001, **kwargs):
        self._initial_balance = initial_balance
        self._slippage_pct = slippage_pct
        self._commission_rate = commission_rate
        self.db: TimingDuckDBProtocol = None
        self.exchange: SimExchange = None
        self.run_id: str = ""
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self.db = TimingDuckDBProtocol.shared()
        if not self.db.adapter: await self.db.on_start()
        self.run_id = os.environ.get("TIMING_RUN_ID", "live_default")
        self.exchange = SimExchange(initial_balance=self._initial_balance,
                                   slippage_pct=self._slippage_pct, commission_rate=self._commission_rate)
        log.info(f'[Broker] DB就绪, run_id={self.run_id}')
        await super().on_start()

    async def on_started(self):
        positions = await self.db.get("positions", run_id=self.run_id)
        count = len(positions) if isinstance(positions, list) else (1 if positions else 0)
        log.info(f'[Broker] 就绪 持仓数={count}')
        await super().on_started()

    async def on_submit_order(self, symbol: str, side: str, order_type: str,
                              quantity: float, price: float, stop_price: float, bar: dict) -> dict | None:
        order = self._create_order(symbol, side, order_type, quantity, price, stop_price, bar.get("ts", 0))
        await self.db.put("orders", order)
        fill = self.exchange.submit_order(order, bar)
        if fill:
            await self._process_fill(fill)
            self._mark_order_filled(order, fill)
            await self.db.put("orders", order)
            return fill
        if order["status"] == "rejected":
            await self.db.put("orders", order)
            try: await hub.dispatch(OrderRejected(order_id=order["order_id"], symbol=symbol, reason="exchange_rejected", ts=bar.get("ts", 0)))
            except Exception: pass
            return None
        await self.db.put("orders", order)
        return None

    async def process_pending(self, bar: dict) -> list[dict]:
        fills = self.exchange.check_pending(bar)
        results = []
        for fill in fills:
            await self._process_fill(fill)
            results.append(fill)
        return results

    async def _process_fill(self, fill: dict):
        pos_data = await self.db.get("positions", run_id=self.run_id, symbol=fill["symbol"])
        if not pos_data:
            pos_data = {"run_id": self.run_id, "symbol": fill["symbol"], "side": "flat",
                        "quantity": 0.0, "avg_entry_price": 0.0, "realized_pnl": 0.0}
        rpnl = self._apply_fill(pos_data, fill)
        await self.db.put("positions", pos_data)
        await self.db.append("fills", {"run_id": self.run_id, "order_id": fill["order_id"],
                                       "symbol": fill["symbol"], "side": fill["side"],
                                       "filled_price": fill["filled_price"],
                                       "filled_quantity": fill["filled_quantity"],
                                       "commission": fill["commission"], "ts": fill["ts"]})
        if rpnl != 0: self.exchange.account_settle(rpnl, 0)
        try:
            await hub.dispatch(OrderFilled(order_id=fill["order_id"], symbol=fill["symbol"], side=fill["side"],
                                           filled_price=fill["filled_price"], filled_quantity=fill["filled_quantity"],
                                           commission=fill["commission"], realized_pnl=rpnl, ts=fill["ts"]))
        except Exception: pass
        log.info(f'[Broker] 成交 {fill["side"]} {fill["symbol"]} qty={fill["filled_quantity"]} rpnl={rpnl:.4f}')

    async def get_position(self, symbol: str) -> dict | None:
        return await self.db.get("positions", run_id=self.run_id, symbol=symbol)

    async def get_all_positions(self) -> list[dict]:
        positions = await self.db.get("positions", run_id=self.run_id)
        return positions if isinstance(positions, list) else ([positions] if positions else [])

    async def get_account(self) -> dict:
        return {"initial_balance": self.exchange.initial_balance, "total": self.exchange.total,
                "net_pnl": self.exchange.total - self.exchange.initial_balance}

    async def on_cancel_order(self, order_id: str) -> dict:
        success = self.exchange.cancel_order(order_id)
        return {"order_id": order_id, "canceled": success}

    async def on_stop(self):
        await super().on_stop()

    # ── 业务逻辑（原 Position.apply_fill / Order.mark_filled）──

    def _create_order(self, symbol: str, side: str, order_type: str, quantity: float,
                      price: float, stop_price: float, ts: int) -> dict:
        return {"run_id": self.run_id, "order_id": uuid.uuid4().hex[:16], "symbol": symbol,
                "side": side, "order_type": order_type, "quantity": quantity, "price": price,
                "stop_price": stop_price, "status": "pending", "fill_price": 0.0,
                "filled_quantity": 0.0, "commission": 0.0, "created_at": ts, "filled_at": 0}

    def _mark_order_filled(self, order: dict, fill: dict):
        order["status"] = "filled"
        order["fill_price"] = fill["filled_price"]
        order["filled_quantity"] = fill["filled_quantity"]
        order["commission"] = fill["commission"]
        order["filled_at"] = fill["ts"]

    def _apply_fill(self, pos: dict, fill: dict) -> float:
        rpnl = 0.0
        side, qty, avg = pos["side"], pos["quantity"], pos["avg_entry_price"]
        f_side, f_qty, f_price = fill["side"], fill["filled_quantity"], fill["filled_price"]

        if side == "flat":
            pos["side"] = "long" if f_side == "buy" else "short"
            pos["quantity"] = f_qty
            pos["avg_entry_price"] = f_price
        elif (side == "long" and f_side == "buy") or (side == "short" and f_side == "sell"):
            total_cost = avg * qty + f_price * f_qty
            pos["quantity"] = qty + f_qty
            pos["avg_entry_price"] = total_cost / pos["quantity"] if pos["quantity"] else 0.0
        else:
            if f_qty >= qty:
                direction = 1 if side == "long" else -1
                rpnl = direction * (f_price - avg) * qty
                remaining = f_qty - qty
                if remaining > 0:
                    pos["side"] = "long" if f_side == "buy" else "short"
                    pos["quantity"] = remaining
                    pos["avg_entry_price"] = f_price
                else:
                    pos["side"] = "flat"
                    pos["quantity"] = 0.0
                    pos["avg_entry_price"] = 0.0
            else:
                direction = 1 if side == "long" else -1
                rpnl = direction * (f_price - avg) * f_qty
                pos["quantity"] = qty - f_qty
        pos["realized_pnl"] = pos.get("realized_pnl", 0.0) + rpnl
        return rpnl
