"""Layer 3: PushBars Command 单元测试 — 使用 run_command，不需要 Hub。"""
import pytest
from unittest.mock import AsyncMock, patch
from bollydog.testing import run_command
from timing.data.models import PushBars


async def test_push_bars_writes_and_returns(sample_bars):
    mock_app = AsyncMock()
    mock_app.append_bars = AsyncMock()
    with patch("timing.data.models.app", mock_app):
        result = await run_command(PushBars(symbol="159363.OF", interval="1d", bars=sample_bars[:3], replay=False))
    assert result["symbol"] == "159363.OF" and result["interval"] == "1d" and len(result["bars"]) == 3
    mock_app.append_bars.assert_called_once()


async def test_push_bars_replay_skips_write(sample_bars):
    mock_app = AsyncMock()
    mock_app.append_bars = AsyncMock()
    with patch("timing.data.models.app", mock_app):
        result = await run_command(PushBars(symbol="159363.OF", interval="1d", bars=sample_bars[:2], replay=True))
    assert result["symbol"] == "159363.OF"
    mock_app.append_bars.assert_not_called()


async def test_push_bars_normalizes_fields():
    mock_app = AsyncMock()
    mock_app.append_bars = AsyncMock()
    with patch("timing.data.models.app", mock_app):
        result = await run_command(PushBars(symbol="T", interval="1m",
                                           bars=[{"open": "1.5", "high": "2", "low": "1", "close": "1.8", "volume": "100", "ts": "1000"}]))
    bar = result["bars"][0]
    assert isinstance(bar["open"], float) and isinstance(bar["ts"], int)
