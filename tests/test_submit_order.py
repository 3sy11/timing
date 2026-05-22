"""Layer 3: Broker.on_submit_order 行为测试 — mock protocol。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from timing.execution.broker import Broker
from timing.models.order import Order, FillResult
from timing.models.account import Account
from timing.models.position import Position


@pytest.fixture
def mock_protocol():
    proto = AsyncMock()
    proto.get_balance = AsyncMock(return_value=Account(initial_balance=100000, total=100000))
    proto.get = AsyncMock(return_value=None)
    proto.set = AsyncMock()
    proto.keys = AsyncMock(return_value=[])
    proto.submit_order = AsyncMock(return_value=FillResult(
        order_id="test123", symbol="159363.OF", side="buy",
        filled_price=1.05, filled_quantity=0.1, commission=0.000105, ts=1000))
    return proto


async def test_market_fill(mock_protocol):
    broker = Broker.__new__(Broker)
    broker._positions = {}
    broker.protocol = mock_protocol
    with patch("timing.execution.broker.hub") as mock_hub:
        mock_hub.dispatch = AsyncMock()
        result = await broker.on_submit_order("159363.OF", "buy", "market", 0.1, 0, 0, {"close": 1.05, "ts": 1000})
    assert result is not None and result["filled_price"] == 1.05
    order_calls = [c for c in mock_protocol.set.call_args_list if "__orders:" in str(c)]
    assert len(order_calls) >= 2
    pos = broker._positions.get("159363.OF")
    assert pos is not None and pos.side == "long" and pos.quantity == 0.1


async def test_limit_order_pending(mock_protocol):
    mock_protocol.submit_order = AsyncMock(return_value=None)
    broker = Broker.__new__(Broker)
    broker._positions = {}
    broker.protocol = mock_protocol
    with patch("timing.execution.broker.hub") as mock_hub:
        mock_hub.dispatch = AsyncMock()
        result = await broker.on_submit_order("T", "buy", "limit", 1.0, 9.5, 0, {"close": 10.0, "ts": 2000})
    assert result is None
    order_calls = [c for c in mock_protocol.set.call_args_list if "__orders:" in str(c)]
    assert len(order_calls) >= 1


async def test_position_apply_fill():
    pos = Position(symbol="T", side="long", quantity=1.0, avg_entry_price=10.0)
    fill = FillResult(order_id="x", symbol="T", side="sell", filled_price=12.0, filled_quantity=1.0, commission=0.012, ts=999)
    rpnl = pos.apply_fill(fill)
    assert rpnl == pytest.approx(2.0) and pos.side == "flat" and pos.realized_pnl == pytest.approx(2.0)
