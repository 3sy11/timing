"""Layer 1: 纯逻辑 — SimExchange + Broker._apply_fill 单元测试。"""
import pytest
from timing.models.exchange import SimExchange
from timing.execution.broker import Broker


class TestSimExchange:
    def test_market_buy_fills(self):
        ex = SimExchange(initial_balance=100000, slippage_pct=0.001)
        order = {"order_id": "o1", "symbol": "T", "side": "buy", "order_type": "market",
                 "quantity": 1.0, "price": 0, "stop_price": 0, "status": "pending"}
        fill = ex.submit_order(order, {"close": 10.0, "ts": 100})
        assert fill is not None
        assert fill["filled_price"] == pytest.approx(10.01, rel=1e-3)
        assert fill["filled_quantity"] == 1.0
        assert order["status"] == "filled"

    def test_market_sell_fills(self):
        ex = SimExchange(initial_balance=100000)
        order = {"order_id": "o2", "symbol": "T", "side": "sell", "order_type": "market",
                 "quantity": 2.0, "price": 0, "stop_price": 0, "status": "pending"}
        fill = ex.submit_order(order, {"close": 20.0, "ts": 200})
        assert fill["filled_price"] < 20.0

    def test_limit_order_pending(self):
        ex = SimExchange(initial_balance=100000)
        order = {"order_id": "o3", "symbol": "T", "side": "buy", "order_type": "limit",
                 "quantity": 1.0, "price": 9.5, "stop_price": 0, "status": "pending"}
        fill = ex.submit_order(order, {"close": 10.0, "ts": 300})
        assert fill is None
        assert len(ex._pending_orders) == 1

    def test_limit_trigger(self):
        ex = SimExchange(initial_balance=100000)
        order = {"order_id": "o4", "symbol": "T", "side": "buy", "order_type": "limit",
                 "quantity": 1.0, "price": 9.5, "stop_price": 0, "status": "pending"}
        ex.submit_order(order, {"close": 10.0, "ts": 300})
        fills = ex.check_pending({"open": 10.0, "high": 10.5, "low": 9.0, "close": 9.5, "ts": 400})
        assert len(fills) == 1

    def test_cancel_order(self):
        ex = SimExchange(initial_balance=100000)
        order = {"order_id": "o5", "symbol": "T", "side": "buy", "order_type": "limit",
                 "quantity": 1.0, "price": 8.0, "stop_price": 0, "status": "pending"}
        ex.submit_order(order, {"close": 10.0, "ts": 500})
        assert ex.cancel_order("o5") is True
        assert len(ex._pending_orders) == 0


class TestBrokerApplyFill:
    def _broker_apply(self, pos: dict, fill: dict) -> float:
        broker = Broker.__new__(Broker)
        return broker._apply_fill(pos, fill)

    def test_flat_to_long(self):
        pos = {"side": "flat", "quantity": 0.0, "avg_entry_price": 0.0, "realized_pnl": 0.0}
        fill = {"side": "buy", "filled_price": 10.0, "filled_quantity": 1.0}
        rpnl = self._broker_apply(pos, fill)
        assert rpnl == 0.0 and pos["side"] == "long" and pos["quantity"] == 1.0

    def test_add_to_long(self):
        pos = {"side": "long", "quantity": 1.0, "avg_entry_price": 10.0, "realized_pnl": 0.0}
        fill = {"side": "buy", "filled_price": 12.0, "filled_quantity": 1.0}
        self._broker_apply(pos, fill)
        assert pos["quantity"] == 2.0 and pos["avg_entry_price"] == pytest.approx(11.0)

    def test_close_long_profit(self):
        pos = {"side": "long", "quantity": 1.0, "avg_entry_price": 10.0, "realized_pnl": 0.0}
        fill = {"side": "sell", "filled_price": 15.0, "filled_quantity": 1.0}
        rpnl = self._broker_apply(pos, fill)
        assert rpnl == pytest.approx(5.0) and pos["side"] == "flat"

    def test_partial_close(self):
        pos = {"side": "long", "quantity": 2.0, "avg_entry_price": 10.0, "realized_pnl": 0.0}
        fill = {"side": "sell", "filled_price": 12.0, "filled_quantity": 1.0}
        rpnl = self._broker_apply(pos, fill)
        assert rpnl == pytest.approx(2.0) and pos["quantity"] == 1.0

    def test_reverse_position(self):
        pos = {"side": "long", "quantity": 1.0, "avg_entry_price": 10.0, "realized_pnl": 0.0}
        fill = {"side": "sell", "filled_price": 12.0, "filled_quantity": 2.0}
        rpnl = self._broker_apply(pos, fill)
        assert rpnl == pytest.approx(2.0) and pos["side"] == "short" and pos["quantity"] == 1.0

    def test_short_close(self):
        pos = {"side": "short", "quantity": 1.0, "avg_entry_price": 10.0, "realized_pnl": 0.0}
        fill = {"side": "buy", "filled_price": 8.0, "filled_quantity": 1.0}
        rpnl = self._broker_apply(pos, fill)
        assert rpnl == pytest.approx(2.0) and pos["side"] == "flat"
