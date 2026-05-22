"""Timing 测试 fixtures — 遵循 bollydog 四层测试模型。

- clean_globals (autouse): 每个测试后清理全局状态
- memory_protocol: 独立 MemoryProtocol 实例
- hub: 完整 E2E Hub（加载 config.toml）
- klines / sample_bars: K 线测试数据
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from bollydog.models.base import BaseService
from bollydog.models.service import AppService
from bollydog.globals import _hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack, _session_ctx_stack, _app_ctx_stack
from timing.data.clients.file import read_file

PARQUET_PATH = str(Path(__file__).resolve().parent.parent / "warehouse" / "ods" / "159363.OF.parquet")
CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "config.toml")
SYMBOL = "159363.OF"
INTERVAL = "1d"


@pytest.fixture(autouse=True)
async def clean_globals():
    yield
    AppService._apps.clear()
    BaseService.registry.clear()
    for stack in (_hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack, _session_ctx_stack, _app_ctx_stack):
        while stack.top is not None:
            stack.pop()


@pytest.fixture
async def memory_protocol():
    from bollydog.adapters.memory import MemoryProtocol
    proto = MemoryProtocol()
    async with proto:
        yield proto



@pytest.fixture(scope="session")
def klines():
    return read_file(PARQUET_PATH)


@pytest.fixture
def sample_bars(klines):
    return klines[:10]
