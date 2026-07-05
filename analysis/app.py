"""AnalysisService — 分析服务 (AppService)。

加载所有 Rules，通过 Analyze command 编排检测流程。
"""
import os
import logging

from bollydog.models.service import AppService

from .rules import discover_rules

log = logging.getLogger(__name__)


class AnalysisService(AppService):
    domain = "analysis"
    alias = "AnalysisService"
    commands = ["timing.analysis.command"]

    def __init__(self, warehouse_path: str = None, **kwargs):
        self.warehouse_path = warehouse_path or os.environ.get("TIMING_WAREHOUSE", "warehouse/timing")
        self._rules = {}
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        self._rules = discover_rules()
        log.info(f'[分析] 已加载 {len(self._rules)} 个 rules: {list(self._rules.keys())}')
        await super().on_start()

    def get_rule(self, rule_name: str) -> dict:
        return self._rules[rule_name]
