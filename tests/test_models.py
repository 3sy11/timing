"""Layer 1: 纯逻辑 — 数据模型单元测试（不需要 Hub）。"""
import pytest
from timing.models.position import Position
from timing.models.account import Account
from timing.models.order import Order, FillResult


class TestPosition:
    def test_flat_to_long(self):
        pos = Position(symbol="T")
        fill = FillResult(order_id="1", symbol="T", side="buy", filled_price=10.0, filled_quantity=1.0, commission=0.01, ts=100)
        rpnl = pos.apply_fill(fill)
        assert rpnl == 0.0
        assert pos.side == "long" and pos.quantity == 1.0 and pos.avg_entry_price == 10.0

    def test_add_to_long(self):
        pos = Position(symbol="T", side="long", quantity=1.0, avg_entry_price=10.0)
        fill = FillResult(order_id="2", symbol="T", side="buy", filled_price=12.0, filled_quantity=1.0, commission=0.012, ts=200)
        pos.apply_fill(fill)
        assert pos.quantity == 2.0 and pos.avg_entry_price == pytest.approx(11.0)

    def test_close_long_profit(self):
        pos = Position(symbol="T", side="long", quantity=1.0, avg_entry_price=10.0)
        fill = FillResult(order_id="3", symbol="T", side="sell", filled_price=15.0, filled_quantity=1.0, commission=0.015, ts=300)
        rpnl = pos.apply_fill(fill)
        assert rpnl == pytest.approx(5.0) and pos.side == "flat" and pos.realized_pnl == pytest.approx(5.0)

    def test_partial_close(self):
        pos = Position(symbol="T", side="long", quantity=2.0, avg_entry_price=10.0)
        fill = FillResult(order_id="4", symbol="T", side="sell", filled_price=12.0, filled_quantity=1.0, commission=0.012, ts=400)
        rpnl = pos.apply_fill(fill)
        assert rpnl == pytest.approx(2.0) and pos.quantity == 1.0 and pos.side == "long"

    def test_reverse_position(self):
        pos = Position(symbol="T", side="long", quantity=1.0, avg_entry_price=10.0)
        fill = FillResult(order_id="5", symbol="T", side="sell", filled_price=12.0, filled_quantity=2.0, commission=0.024, ts=500)
        rpnl = pos.apply_fill(fill)
        assert rpnl == pytest.approx(2.0) and pos.side == "short" and pos.quantity == 1.0

    def test_short_close(self):
        pos = Position(symbol="T", side="short", quantity=1.0, avg_entry_price=10.0)
        fill = FillResult(order_id="6", symbol="T", side="buy", filled_price=8.0, filled_quantity=1.0, commission=0.008, ts=600)
        rpnl = pos.apply_fill(fill)
        assert rpnl == pytest.approx(2.0) and pos.side == "flat"


class TestAccount:
    def test_initial_state(self):
        acc = Account(initial_balance=10000, total=10000)
        assert acc.free == 10000 and acc.net_pnl == 0.0

    def test_settle_profit(self):
        acc = Account(initial_balance=10000, total=10000)
        acc.settle(pnl=500.0, commission=10.0)
        assert acc.total == pytest.approx(10490.0) and acc.net_pnl == pytest.approx(490.0)

    def test_settle_loss(self):
        acc = Account(initial_balance=10000, total=10000)
        acc.settle(pnl=-200.0, commission=5.0)
        assert acc.total == pytest.approx(9795.0)


class TestOrder:
    def test_mark_filled(self):
        order = Order(symbol="T", side="buy", quantity=1.0)
        order.mark_filled(price=10.0, qty=1.0, commission=0.01, ts=999)
        assert order.status == "filled" and order.filled_price == 10.0 and order.updated_at == 999

    def test_order_id_unique(self):
        o1, o2 = Order(symbol="A"), Order(symbol="B")
        assert o1.order_id != o2.order_id and len(o1.order_id) == 16
