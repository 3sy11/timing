"""Layer 4: RunBacktest E2E — 使用 run_execute 轻量执行。"""
import os, shutil
from pathlib import Path
import pytest
from bollydog.testing import run_execute
from timing.engine.command import RunBacktest, MergeBacktest
from timing.adapters.duckdb import TimingDuckDBProtocol

CONFIG_BT = str(Path(__file__).resolve().parent.parent / "config_backtest.toml")
BACKTEST_TOML = str(Path(__file__).resolve().parent.parent / "backtest.toml")
TMP_DATA = "/tmp/timing_test_bt"


@pytest.fixture(autouse=True)
def clean_tmp():
    if os.path.exists(TMP_DATA): shutil.rmtree(TMP_DATA)
    os.makedirs(TMP_DATA, exist_ok=True)
    os.environ["TIMING_DATA_ROOT"] = TMP_DATA
    TimingDuckDBProtocol.reset_shared()
    yield
    TimingDuckDBProtocol.reset_shared()
    if os.path.exists(TMP_DATA): shutil.rmtree(TMP_DATA)


async def test_run_backtest_produces_signals():
    """RunBacktest 读 backtest.toml 配置，串行执行，写临时文件。"""
    async with run_execute(CONFIG_BT) as executor:
        msg = RunBacktest(backtest_config=BACKTEST_TOML, run_id="test_e2e")
        result = await executor.execute(msg)
    assert result is not None
    assert result["run_id"] == "test_e2e"
    assert result["signals_count"] > 0
    assert os.path.exists(result["tmp_path"])


async def test_merge_backtest():
    """先执行 RunBacktest，再执行 MergeBacktest 合并到主库。"""
    async with run_execute(CONFIG_BT) as executor:
        msg = RunBacktest(backtest_config=BACKTEST_TOML, run_id="merge_test")
        bt_result = await executor.execute(msg)

    TimingDuckDBProtocol.reset_shared()
    async with run_execute(CONFIG_BT) as executor:
        merge_result = await executor.execute(MergeBacktest())
    assert merge_result["files"] == 1
    assert merge_result["merged"] > 0
