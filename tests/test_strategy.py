"""Layer 3: FibStrategy 行为测试 — mock db + broker。"""
from unittest.mock import AsyncMock, MagicMock
from timing.strategy.app import FibStrategy


def _make_strategy():
    svc = FibStrategy.__new__(FibStrategy)
    svc.position_size = 0.1
    svc.min_strength = 0.6
    svc.db = AsyncMock()
    svc.db.put = AsyncMock()
    svc.run_id = "test_run"
    svc.depends = {}
    return svc


def _make_signal_cmd(direction="long", strength=0.8, symbol="T", price=10.0, ts=1000):
    cmd = MagicMock()
    cmd.symbol = symbol
    cmd.direction = direction
    cmd.strength = strength
    cmd.price = price
    cmd.ts = ts
    return cmd


async def test_strong_signal_submits_order():
    svc = _make_strategy()
    mock_broker = AsyncMock()
    mock_broker.on_submit_order = AsyncMock()
    svc.depends = {"execution.Broker": mock_broker}
    cmd = _make_signal_cmd(direction="long", strength=0.8, price=10.0)
    await svc.on_signal(cmd)
    svc.db.put.assert_called()
    call_data = svc.db.put.call_args[0][1]
    assert call_data["action"] == "submit"
    mock_broker.on_submit_order.assert_called_once()


async def test_weak_signal_skips():
    svc = _make_strategy()
    cmd = _make_signal_cmd(direction="long", strength=0.3)
    await svc.on_signal(cmd)
    call_data = svc.db.put.call_args[0][1]
    assert call_data["action"] == "skip" and "strength" in call_data["reason"]


async def test_neutral_skips():
    svc = _make_strategy()
    cmd = _make_signal_cmd(direction="neutral", strength=0.9)
    await svc.on_signal(cmd)
    call_data = svc.db.put.call_args[0][1]
    assert call_data["action"] == "skip" and "neutral" in call_data["reason"]
