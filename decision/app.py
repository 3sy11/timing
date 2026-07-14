"""DecisionService — 决策服务（规则引擎）。"""
import os, logging
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class DecisionService(AppService):
    domain = "decision"
    alias = "DecisionService"
    commands = ["timing.decision.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        log.info(f'[决策] warehouse={self.warehouse_path}')
        await super().on_start()
