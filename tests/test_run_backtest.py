"""Layer 4: E2E 测试 — 通过 run_hub 完整执行。

Hub.__aexit__ 在 mode.Service 中可能挂起（Queue consumer），
使用 asyncio.wait_for 确保测试不卡死。
"""
import asyncio
import shutil
from pathlib import Path
import pytest
from bollydog.testing import run_hub
from bollydog.models.base import BaseService
from bollydog.models.service import AppService
from bollydog.globals import _hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack, _session_ctx_stack, _app_ctx_stack

CONFIG = str(Path(__file__).resolve().parent.parent / "config.toml")
PARQUET = str(Path(__file__).resolve().parent.parent / "warehouse" / "ods" / "159363.OF.parquet")
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


@pytest.fixture(autouse=True)
def clean_env():
    if CACHE_DIR.exists(): shutil.rmtree(CACHE_DIR)
    yield
    if CACHE_DIR.exists(): shutil.rmtree(CACHE_DIR)
    AppService._apps.clear()
    BaseService.registry.clear()
    for stack in (_hub_ctx_stack, _protocol_ctx_stack, _message_ctx_stack, _session_ctx_stack, _app_ctx_stack):
        while stack.top is not None:
            stack.pop()


async def test_import_and_backtest():
    """E2E: ImportKlines → RunBacktest 完整流程。"""
    from bollydog.service import load_from_config
    from bollydog.service.app import Hub
    from timing.data.models import ImportKlines
    from timing.engine.command import RunBacktest

    load_from_config(CONFIG)
    hub = AppService._apps['bollydog.Hub']
    await hub.start()

    result = await hub.execute(ImportKlines(symbol="159363.OF", interval="1d", path=PARQUET))
    assert result["count"] == 328

    result = await hub.execute(RunBacktest(symbol="159363.OF", interval="1d"))
    assert result is not None
    assert result["klines_total"] == 328 and result["errors"] == 0
    assert len(result["decisions"]) > 0
    assert len(result["fills"]) > 0
    assert result["account"]["total"] != result["account"]["initial_balance"]

    # 不调用 hub.stop()，让 clean_env fixture 清理全局状态
