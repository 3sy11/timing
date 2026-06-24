"""Timing 测试 fixtures — 遵循 bollydog 四层测试模型。

- clean_globals (autouse): 每个测试后清理全局状态
- klines / sample_bars: K 线测试数据
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("TIMING_DATA_ROOT", "/tmp/timing_test")

import pytest
from bollydog.globals import (
    _hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack,
    _session_ctx_stack, _app_ctx_stack, _services_ctx_stack, _registry_ctx_stack,
)
from timing.data.clients.file import read_file
from timing.adapters.duckdb import TimingDuckDBProtocol

PARQUET_PATH = str(Path(__file__).resolve().parent.parent / "warehouse" / "ods" / "159363.OF.parquet")
CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "config.toml")
CONFIG_BACKTEST_PATH = str(Path(__file__).resolve().parent.parent / "config_backtest.toml")
SYMBOL = "159363.OF"
INTERVAL = "1d"


@pytest.fixture(autouse=True)
async def clean_globals():
    yield
    TimingDuckDBProtocol.reset_shared()
    for stack in (_hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack,
                  _session_ctx_stack, _app_ctx_stack, _services_ctx_stack, _registry_ctx_stack):
        while stack.top is not None:
            stack.pop()


@pytest.fixture(scope="session")
def klines():
    return read_file(PARQUET_PATH)


@pytest.fixture
def sample_bars(klines):
    return klines[:10]
