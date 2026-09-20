"""Analyze command — 读特征宽表，按规则写出 signals。

当前没有已注册规则；先把 feature_id 指到宽表，规则落地后再跑。
"""
import logging
from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Analyze(BaseCommand):
    destination: ClassVar[str] = "analysis.AnalysisService.Analyze"
    rule: str = ""
    feature_id: str = ""
    analysis_id: str = ""
    symbol: str = ""
    interval: str = ""
    profile: str = ""
    override: str = ""

    async def __call__(self) -> dict | None:
        if not (self.rule and self.feature_id and self.analysis_id and self.symbol and self.interval):
            log.error('[分析] Analyze 缺少必要参数: rule, feature_id, analysis_id, symbol, interval')
            return None

        rules = getattr(app, "_rules", None) or {}
        try:
            rule_meta = app.get_rule(self.rule)
        except (KeyError, AttributeError):
            rule_meta = rules.get(self.rule)
        if not rule_meta:
            log.error(
                f'[分析] 无规则 {self.rule!r}。特征宽表在 features/{self.feature_id}/，'
                f'规则尚未注册，不写出 signals。'
            )
            return None

        proto = app.protocol
        features = proto.read_candidates(self.feature_id, self.symbol, self.interval)
        if features.empty:
            log.error(f'[分析] 无特征宽表: features/{self.feature_id}/{self.symbol}/{self.interval}')
            return None
        log.error('[分析] 规则已注册但尚未实现检测函数，中止')
        return None
