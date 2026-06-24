"""Layer 3: ImportKlines Command 单元测试 — 使用 run_command。"""
from unittest.mock import AsyncMock
from bollydog.testing import run_command
from timing.data.models import ImportKlines
from timing.tests.conftest import PARQUET_PATH


async def test_import_klines_from_parquet():
    mock_app = AsyncMock()
    mock_app.set_klines = AsyncMock()
    result = await run_command(ImportKlines(symbol="159363.OF", interval="1d", path=PARQUET_PATH), app=mock_app)
    assert result == {"symbol": "159363.OF", "interval": "1d", "count": 328}
    mock_app.set_klines.assert_called_once()


async def test_import_klines_destination():
    assert ImportKlines.destination == "data.DataEngine.ImportKlines"
