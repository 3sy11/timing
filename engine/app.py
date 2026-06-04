"""TimingApp / BacktestApp — 生产/回测两个入口。"""
import os, logging
from bollydog.models.service import AppService
from bollydog.globals import hub

log = logging.getLogger(__name__)


class TimingApp(AppService):
    """生产入口 — 纯容器，靠 TOML depends 把各服务串起来。"""
    domain = "timing"
    alias = "TimingApp"
    commands = []


class BacktestApp(AppService):
    """回测入口 — 提供 RunBacktest / MergeBacktest 命令。

    RunBacktest: 读指定 backtest toml → 串行执行 → 写临时文件
    MergeBacktest: 合并所有临时文件到主库

    多进程并行：开多个终端 execute 不同 toml 即可。
    """
    domain = "backtest"
    alias = "BacktestApp"
    commands = ["timing.engine.command"]

    def __init__(self, backtest_config="backtest.toml", ods_dir="warehouse/ods", **kwargs):
        self._bt_config_path = backtest_config
        self._ods_dir = ods_dir
        super().__init__(**kwargs)

    def on_init_dependencies(self):
        return []

    async def on_started(self):
        await self._ensure_data()
        await super().on_started()

    async def _ensure_data(self):
        """检查主库品种数据，缺失则自动导入。无 DataEngine 或锁冲突则跳过。"""
        from bollydog.models.service import BaseService
        if "data.DataEngine.ImportKlines" not in BaseService.registry:
            log.info('[回测] 无 DataEngine，跳过数据导入（RunBacktest 从 parquet 直读）'); return
        from timing.adapters.duckdb import TimingDuckDBProtocol
        from timing.data.models import ImportKlines
        db = TimingDuckDBProtocol.shared()
        try:
            if not db.adapter:
                await db.on_start()
        except Exception as e:
            log.info(f'[回测] 跳过数据导入（主库锁: {e}），回测从 parquet 直读'); return

        if not os.path.isdir(self._ods_dir):
            return
        parquets = [f for f in os.listdir(self._ods_dir) if f.endswith(".parquet")]
        for fname in sorted(parquets):
            sym = fname.replace(".parquet", "")
            try:
                cnt = db.adapter.execute(
                    'SELECT COUNT(*) FROM klines WHERE symbol=? AND "interval"=?', [sym, "1d"]).fetchone()[0]
                if cnt > 0:
                    continue
            except Exception:
                continue
            path = os.path.join(self._ods_dir, fname)
            log.info(f'[回测] 自动导入 {sym}/1d ← {path}')
            await hub.execute(ImportKlines(path=path, symbol=sym, interval="1d"))
