"""ExecutionService — 执行服务，编排订单提交与成交处理。"""
import os, logging
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class ExecutionService(AppService):
    domain = "execution"
    alias = "ExecutionService"
    commands = ["timing.execution.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        log.info(f'[执行] warehouse={self.warehouse_path}')
        await super().on_start()
