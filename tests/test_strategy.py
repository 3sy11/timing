"""Layer 3: FibStrategy 行为测试 — mock protocol + hub。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from timing.strategy.app import FibStrategy
from timing.strategy.models import StrategyDecision


def _make_strategy():
    svc = FibStrategy.__new__(FibStrategy)
    svc.position_size = 0.1
    svc.min_strength = 0.6
    svc.protocol = AsyncMock()
    svc.protocol.get = AsyncMock(return_value=[])
    svc.protocol.set = AsyncMock()
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
    cmd = _make_signal_cmd(direction="long", strength=0.8, price=10.0)
    with patch("timing.strategy.app.hub") as mock_hub:
        mock_hub.execute = AsyncMock()
        await svc.on_signal(cmd)
    svc.protocol.set.assert_called()
    decisions = svc.protocol.set.call_args[0][1]
    assert len(decisions) == 1 and decisions[0]["action"] == "submit"
    mock_hub.execute.assert_called_once()


async def test_weak_signal_skips():
    svc = _make_strategy()
    cmd = _make_signal_cmd(direction="long", strength=0.3)
    with patch("timing.strategy.app.hub") as mock_hub:
        mock_hub.execute = AsyncMock()
        await svc.on_signal(cmd)
    decisions = svc.protocol.set.call_args[0][1]
    assert decisions[0]["action"] == "skip" and "strength" in decisions[0]["reason"]
    mock_hub.execute.assert_not_called()


async def test_neutral_skips():
    svc = _make_strategy()
    cmd = _make_signal_cmd(direction="neutral", strength=0.9)
    with patch("timing.strategy.app.hub") as mock_hub:
        mock_hub.execute = AsyncMock()
        await svc.on_signal(cmd)
    decisions = svc.protocol.set.call_args[0][1]
    assert decisions[0]["action"] == "skip" and "neutral" in decisions[0]["reason"]


def test_decision_model_frozen():
    d = StrategyDecision(ts=100, symbol="T", direction="long", strength=0.7, price=10, action="submit", reason="buy")
    assert d.ts == 100 and d.model_config["frozen"] is True
