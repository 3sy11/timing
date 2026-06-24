"""Layer 3: Broker.on_submit_order 行为测试 — mock db + exchange。"""
import pytest
from unittest.mock import AsyncMock, patch
from timing.execution.broker import Broker
from timing.models.exchange import SimExchange


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.put = AsyncMock()
    db.append = AsyncMock()
    return db


async def test_market_fill(mock_db):
    broker = Broker.__new__(Broker)
    broker.db = mock_db
    broker.run_id = "test"
    broker.exchange = SimExchange(initial_balance=100000)
    with patch("timing.execution.broker.hub") as mock_hub:
        mock_hub.dispatch = AsyncMock()
        result = await broker.on_submit_order("159363.OF", "buy", "market", 0.1, 0, 0, {"close": 1.05, "ts": 1000})
    assert result is not None and result["filled_price"] > 0
    put_calls = [c for c in mock_db.put.call_args_list if c[0][0] == "orders"]
    assert len(put_calls) >= 2
    pos_calls = [c for c in mock_db.put.call_args_list if c[0][0] == "positions"]
    assert len(pos_calls) >= 1


async def test_limit_order_pending(mock_db):
    broker = Broker.__new__(Broker)
    broker.db = mock_db
    broker.run_id = "test"
    broker.exchange = SimExchange(initial_balance=100000)
    with patch("timing.execution.broker.hub") as mock_hub:
        mock_hub.dispatch = AsyncMock()
        result = await broker.on_submit_order("T", "buy", "limit", 1.0, 9.5, 0, {"close": 10.0, "ts": 2000})
    assert result is None
    assert len(broker.exchange._pending_orders) == 1
