"""DashboardService — 后管服务，挂载静态前端 + 管理回测运行记录。"""
import os, time, logging, itertools
from bollydog.models.service import AppService
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.globals import hub
from .models import BatchJob, BacktestRun, BacktestProgress
from timing.common.metrics import compute_metrics

log = logging.getLogger(__name__)
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


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

    def __init__(self, cache_path: str = "cache/dashboard", **kwargs):
        self._cache_path = cache_path
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        os.makedirs(self._cache_path, exist_ok=True)
        if not self.protocol:
            inner = SQLiteProtocol(path=os.path.join(self._cache_path, "dashboard.sqlite"))
            cache = CacheLayer(flush_threshold=1)
            cache.add_dependency(inner)
            self.protocol = cache
            await cache.maybe_start()
        await super().on_start()

    async def on_started(self) -> None:
        # 延迟挂载静态文件：等所有服务启动完毕后再 mount（避免 HttpService 路由日志遍历 Mount 对象报错）
        import asyncio
        asyncio.ensure_future(self._mount_static_delayed())
        await super().on_started()

    async def _mount_static_delayed(self):
        import asyncio
        await asyncio.sleep(0.5)  # 确保 HttpService.on_started 完成
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
        """异步执行批量回测任务，逐组广播进度。"""
        from timing.engine.command import RunBacktest
        from timing.analysis.app import AnalysisEngine
        from timing.strategy.app import FibStrategy

        grid = job.param_grid
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        job.total_runs = len(combos)
        job.status = "running"
        await self.protocol.set("__current_job", job.model_dump())

        strategy_keys = {"position_size", "min_strength"}

        for idx, combo in enumerate(combos):
            run = BacktestRun(symbol=job.symbol, interval=job.interval, params=combo, status="running")
            job.runs.append(run.run_id)

            # 应用参数
            analysis_params = {k: v for k, v in combo.items() if k not in strategy_keys}
            strategy_params = {k: v for k, v in combo.items() if k in strategy_keys}
            for svc in AnalysisEngine._services.values():
                if analysis_params:
                    if isinstance(svc.config, dict): svc.config.update(analysis_params)
                    else: svc.config = analysis_params
            for svc in FibStrategy._apps.values():
                if "position_size" in strategy_params: svc.position_size = strategy_params["position_size"]
                if "min_strength" in strategy_params: svc.min_strength = strategy_params["min_strength"]

            # 重置状态
            await self._reset_state(job.symbol, job.interval)

            # 广播进度：running
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
                    m.pop("equity_curve", None)  # 不存到摘要里
                    run.metrics = m
                    run.status = "completed"
                    # 存储详细结果（含 klines）
                    await self.protocol.set(f"__run_detail:{run.run_id}", {
                        **run.model_dump(), "result": result})
                else:
                    run.status = "failed"
                    run.error = "no result"
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                log.error(f'[Dashboard] run {run.run_id} 失败: {e}')

            run.completed_at = int(time.time() * 1000)
            job.completed_runs = idx + 1

            # 广播进度：completed/failed
            await hub.dispatch(BacktestProgress(
                job_id=job.job_id, run_index=idx, total_runs=job.total_runs,
                status=run.status, params=combo, metrics=run.metrics, run_id=run.run_id))

            # 保存 run 到列表
            all_runs = await self.protocol.get("__runs") or []
            all_runs.append(run.model_dump())
            await self.protocol.set("__runs", all_runs)
            await self.protocol.set("__current_job", job.model_dump())

        job.status = "completed"
        await self.protocol.set("__current_job", job.model_dump())
        log.info(f'[Dashboard] 批量回测完成 job={job.job_id} {job.completed_runs}/{job.total_runs}')

    async def _reset_state(self, symbol: str, interval: str):
        """重置分析/策略/Broker 状态。"""
        from timing.analysis.app import AnalysisEngine
        from timing.strategy.app import FibStrategy
        from bollydog.globals import app as _app

        for svc in AnalysisEngine._services.values():
            if not svc.protocol: continue
            for key in [f"__ckpt:{symbol}:{interval}", f"signals:{symbol}:{interval}",
                        f"_touch:{symbol}:{interval}", f"retracement:{symbol}:{interval}"]:
                await svc.protocol.remove(key)
        for svc in FibStrategy._apps.values():
            if hasattr(svc, 'protocol') and svc.protocol:
                await svc.protocol.remove(f"decisions:{symbol}")

        broker = next((s for s in (_app._children or []) if getattr(s, 'alias', '') == 'Broker'), None)
        if broker and broker.protocol:
            for fk in (await broker.protocol.keys("__fills:*") or []):
                await broker.protocol.remove(fk)
            for ok in (await broker.protocol.keys("__orders:*") or []):
                await broker.protocol.remove(ok)
            await broker.protocol.remove("__positions")
            broker._positions = {}
            exchange = broker.protocol
            if hasattr(exchange, 'account') and hasattr(exchange, '_initial_balance'):
                exchange.account = type(exchange.account)(initial_balance=exchange._initial_balance, total=exchange._initial_balance)
                exchange._pending_orders = []
