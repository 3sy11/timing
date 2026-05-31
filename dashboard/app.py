"""DashboardService — 后管服务，挂载静态前端 + 管理回测运行记录。"""
import os, time, logging, itertools
from bollydog.models.service import AppService
from bollydog.globals import hub
from timing.adapters.sqlite import TableSchema, StructuredSQLiteProtocol
from timing.dashboard.models import BatchJob, BacktestRun, RunDetail, Dataset, BacktestProgress
from timing.common.metrics import compute_metrics

log = logging.getLogger(__name__)
DATA_ROOT = os.environ.get("TIMING_DATA_ROOT", "warehouse/timing")
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")

DASHBOARD_SCHEMAS = [
    TableSchema(model=BatchJob, table="jobs", key_columns=["job_id"]),
    TableSchema(model=BacktestRun, table="runs", key_columns=["run_id"]),
    TableSchema(model=RunDetail, table="run_details", key_columns=["run_id"]),
    TableSchema(model=Dataset, table="datasets", key_columns=["symbol", "interval"]),
]


class DashboardService(AppService):
    domain = "dashboard"
    alias = "DashboardService"
    commands = ["timing.dashboard.commands"]
    router_mapping = {
        "GetStatus": ["GET", "/api/dashboard/status"],
        "ListRuns": ["GET", "/api/dashboard/runs"],
        "GetRun": ["GET", "/api/dashboard/runs/{run_id}"],
        "StartBatch": ["POST", "/api/dashboard/batch"],
        "ListDatasets": ["GET", "/api/dashboard/datasets"],
        "UploadData": ["POST", "/api/dashboard/upload"],
    }

    def __init__(self, cache_path: str = None, **kwargs):
        self._cache_path = cache_path or DATA_ROOT
        self.db: StructuredSQLiteProtocol = None
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        os.makedirs(self._cache_path, exist_ok=True)
        db_path = os.path.join(self._cache_path, "dashboard.sqlite")
        self.db = StructuredSQLiteProtocol(path=db_path, schemas=DASHBOARD_SCHEMAS)
        await self.db.on_start()
        log.info(f'[Dashboard] DB就绪: {db_path}')
        await super().on_start()

    async def on_started(self) -> None:
        import asyncio
        asyncio.ensure_future(self._mount_static_delayed())
        await super().on_started()

    async def _mount_static_delayed(self):
        import asyncio
        await asyncio.sleep(0.5)
        try:
            from starlette.staticfiles import StaticFiles
            http_svc = next((s for s in AppService._apps.values() if getattr(s, 'alias', '') == 'HttpService'), None)
            if http_svc and os.path.isdir(WEB_DIR):
                http_svc.http_app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web_static")
                log.info(f'[Dashboard] 静态文件挂载 {WEB_DIR} → /')
            else:
                log.warning(f'[Dashboard] HttpService 或 web/ 目录不存在，跳过静态挂载')
        except Exception as e:
            log.warning(f'[Dashboard] 静态挂载失败: {e}')

    async def run_batch_job(self, job: BatchJob):
        from timing.engine.command import RunBacktest
        from timing.analysis.app import AnalysisEngine
        from timing.strategy.app import FibStrategy

        grid = job.param_grid
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        job.total_runs = len(combos)
        job.status = "running"
        await self.db.put("jobs", job.model_dump(), job_id=job.job_id)

        strategy_keys = {"position_size", "min_strength"}

        for idx, combo in enumerate(combos):
            run = BacktestRun(symbol=job.symbol, interval=job.interval, params=combo, status="running")
            job.runs.append(run.run_id)

            analysis_params = {k: v for k, v in combo.items() if k not in strategy_keys}
            strategy_params = {k: v for k, v in combo.items() if k in strategy_keys}
            for svc in AnalysisEngine._services.values():
                if analysis_params:
                    if isinstance(svc.config, dict): svc.config.update(analysis_params)
                    else: svc.config = analysis_params
            for svc in FibStrategy._apps.values():
                if "position_size" in strategy_params: svc.position_size = strategy_params["position_size"]
                if "min_strength" in strategy_params: svc.min_strength = strategy_params["min_strength"]

            await self._reset_state(job.symbol, job.interval)

            await hub.dispatch(BacktestProgress(
                job_id=job.job_id, run_index=idx, total_runs=job.total_runs,
                status="running", params=combo, run_id=run.run_id))

            try:
                result = await hub.execute(RunBacktest(
                    symbol=job.symbol, interval=job.interval, warmup_bars=job.warmup_bars))
                if result:
                    fills = result.get("fills", [])
                    account = result.get("account", {})
                    initial = account.get("initial_balance", 100000)
                    final = account.get("total", initial)
                    m = compute_metrics(fills, initial, final)
                    m.pop("equity_curve", None)
                    run.metrics = m
                    run.status = "completed"
                    await self.db.put("run_details", {"run_id": run.run_id, "data": result}, run_id=run.run_id)
                else:
                    run.status = "failed"
                    run.error = "no result"
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                log.error(f'[Dashboard] run {run.run_id} 失败: {e}')

            run.completed_at = int(time.time() * 1000)
            job.completed_runs = idx + 1

            await hub.dispatch(BacktestProgress(
                job_id=job.job_id, run_index=idx, total_runs=job.total_runs,
                status=run.status, params=combo, metrics=run.metrics, run_id=run.run_id))

            await self.db.put("runs", run.model_dump(), run_id=run.run_id)
            await self.db.put("jobs", job.model_dump(), job_id=job.job_id)

        job.status = "completed"
        await self.db.put("jobs", job.model_dump(), job_id=job.job_id)
        log.info(f'[Dashboard] 批量回测完成 job={job.job_id} {job.completed_runs}/{job.total_runs}')

    async def _reset_state(self, symbol: str, interval: str):
        """重置分析/策略/Broker 状态。"""
        from timing.analysis.app import AnalysisEngine
        from timing.strategy.app import FibStrategy
        from bollydog.globals import app as _app

        for svc in AnalysisEngine._services.values():
            if not svc.db: continue
            await svc.db.delete("checkpoints", symbol=symbol, interval=interval)
            await svc.db.delete("signals", symbol=symbol, interval=interval)
            await svc.db.delete("touches", symbol=symbol, interval=interval)
            await svc.db.delete("retracements", symbol=symbol, interval=interval)

        for svc in FibStrategy._apps.values():
            if hasattr(svc, 'db') and svc.db:
                await svc.db.delete("decisions", symbol=symbol)

        broker = next((s for s in (_app._children or []) if getattr(s, 'alias', '') == 'Broker'), None)
        if broker and broker.db:
            await broker.db.clear("fills")
            await broker.db.clear("orders")
            await broker.db.clear("positions")
            broker.exchange.reset()

    async def on_stop(self):
        if self.db: await self.db.on_stop()
        await super().on_stop()
