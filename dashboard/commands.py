"""Dashboard API 命令。"""
import asyncio, time, logging
from typing import Any, ClassVar
from pydantic import Field
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from .models import BatchJob, BacktestProgress

log = logging.getLogger(__name__)


class GetStatus(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.GetStatus"

    async def __call__(self, *args, **kwargs) -> Any:
        from bollydog.models.service import AppService
        services = []
        for name, svc in AppService._apps.items():
            services.append({"alias": getattr(svc, 'alias', name), "domain": getattr(svc, 'domain', ''),
                             "running": svc._started.is_set() if hasattr(svc, '_started') else False})
        svc = _get_dashboard()
        current_job = None
        if svc and svc.db:
            jobs = await svc.db.all("jobs")
            current_job = next((j for j in reversed(jobs) if j["status"] == "running"), None)
        return {"services": services, "current_job": current_job}


class ListRuns(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.ListRuns"
    limit: int = 50
    offset: int = 0

    async def __call__(self, *args, **kwargs) -> Any:
        svc = _get_dashboard()
        if not svc or not svc.db: return {"runs": [], "total": 0}
        all_runs = await svc.db.all("runs")
        all_runs.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return {"runs": all_runs[self.offset:self.offset + self.limit], "total": len(all_runs)}


class GetRun(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.GetRun"
    run_id: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        svc = _get_dashboard()
        if not svc or not svc.db: return None
        return await svc.db.get("run_details", run_id=self.run_id)


class StartBatch(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.StartBatch"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    param_grid: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        svc = _get_dashboard()
        if not svc: return {"error": "DashboardService not found"}
        job = BatchJob(symbol=self.symbol, interval=self.interval,
                       warmup_bars=self.warmup_bars, param_grid=self.param_grid)
        await svc.db.put("jobs", job.model_dump(), job_id=job.job_id)
        log.info(f'[Dashboard] 启动批量回测 job={job.job_id} {self.symbol}/{self.interval}')
        asyncio.ensure_future(svc.run_batch_job(job))
        return {"job_id": job.job_id, "status": "started"}


class ListDatasets(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.ListDatasets"

    async def __call__(self, *args, **kwargs) -> Any:
        svc = _get_dashboard()
        if not svc or not svc.db: return {"datasets": []}
        datasets = await svc.db.all("datasets")
        return {"datasets": datasets}


class UploadData(BaseCommand):
    destination: ClassVar[str] = "dashboard.DashboardService.UploadData"
    symbol: str = ""
    interval: str = ""
    file: dict = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        if not self.file or not self.file.get("file"):
            return {"error": "no file"}
        from timing.data.clients.file import read_file
        from timing.data.app import DataEngine
        import tempfile, os
        suffix = os.path.splitext(self.file.get("filename", "data.parquet"))[1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(self.file["file"])
            tmp_path = tmp.name
        try:
            klines = read_file(tmp_path)
        finally:
            os.unlink(tmp_path)
        if not klines: return {"error": "empty or invalid file"}
        data_svc = next((s for s in DataEngine._apps.values() if isinstance(s, DataEngine)), None)
        if data_svc:
            await data_svc.set_klines(self.symbol, self.interval, klines)
        svc = _get_dashboard()
        if svc and svc.db:
            await svc.db.put("datasets", {"symbol": self.symbol, "interval": self.interval,
                                          "count": len(klines), "filename": self.file.get("filename", ""),
                                          "uploaded_at": int(time.time() * 1000)},
                             symbol=self.symbol, interval=self.interval)
        log.info(f'[Dashboard] 数据导入 {self.symbol}/{self.interval} {len(klines)}条')
        return {"symbol": self.symbol, "interval": self.interval, "count": len(klines)}


def _get_dashboard():
    from .app import DashboardService
    return next((s for s in DashboardService._apps.values() if isinstance(s, DashboardService)), None)
