"""FeatureService — 特征服务。调度宽表作业，写出 features/{feature_id}/。"""
import os
import logging

from bollydog.models.service import AppService

from .jobs import discover_jobs

log = logging.getLogger(__name__)


class FeatureService(AppService):
    domain = "feature"
    alias = "FeatureService"
    commands = ["timing.feature.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        self._jobs = {}
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self._jobs = discover_jobs()
        log.info(f'[特征] 已加载 {len(self._jobs)} 个 jobs: {list(self._jobs.keys())}')
        await super().on_start()

    def get_job(self, job_name: str) -> dict:
        return self._jobs[job_name]
