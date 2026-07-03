"""ComputationService — 计算服务。调度算法管道，产出中间表和投产表。"""
import os, logging
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class ComputationService(AppService):
    domain = "computation"
    alias = "ComputationService"
    commands = ["timing.computation.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        log.info(f'[计算] warehouse={self.warehouse_path}')
        await super().on_start()
