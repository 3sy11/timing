"""Analyze command — 分析模块 CLI 入口。

流程：resolve rule → load profile → read data via protocol → detect → write signals
"""
import logging
from datetime import datetime, timezone
from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Analyze(BaseCommand):
    """触发指定 Rule 对指定 compute_id 产出的分析检测。"""
    destination: ClassVar[str] = "analysis.AnalysisService.Analyze"
    rule: str = ""
    compute_id: str = ""
    analysis_id: str = ""
    symbol: str = ""
    interval: str = ""
    profile: str = ""
    override: str = ""

    async def __call__(self) -> dict | None:
        if not (self.rule and self.compute_id and self.analysis_id and self.symbol and self.interval):
            log.error('[分析] Analyze 缺少必要参数: rule, compute_id, analysis_id, symbol, interval')
            return None

        rule_meta = app.get_rule(self.rule)
        config_class = rule_meta["config_class"]
        detect_fn = rule_meta["detect_fn"]
        upstream_algo = rule_meta["upstream_algo"]

        profile_name = self.profile or "default"
        override_list = [s.strip() for s in self.override.split(",") if s.strip()] if self.override else []
        cfg = config_class.from_profile(profile_name, override_list)

        proto = app.protocol
        sorted_ts, ts_groups = proto.read_structures_timeseries(
            upstream_algo, self.compute_id, self.symbol, self.interval)
        if not sorted_ts:
            log.error(f'[分析] 无结构数据: {upstream_algo}/{self.compute_id}/{self.symbol}/{self.interval}')
            return None

        klines = proto.read_klines(self.symbol, self.interval)
        if not klines:
            log.error(f'[分析] 无 klines: {self.symbol}/{self.interval}')
            return None

        def groups_resolver(bar_ts):
            return proto.get_groups_at(sorted_ts, ts_groups, bar_ts)

        log.info(f'[分析] 开始检测 rule={self.rule} analysis_id={self.analysis_id} '
                 f'ts_points={len(sorted_ts)} klines={len(klines)}')
        result = detect_fn(klines, [], cfg=cfg, groups_resolver=groups_resolver)

        proto.write_signals(result["signals"], self.analysis_id, self.symbol, self.interval)

        manifest = {
            "analysis_id": self.analysis_id,
            "rule": self.rule,
            "upstream_algo": upstream_algo,
            "compute_id": self.compute_id,
            "symbol": self.symbol,
            "interval": self.interval,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "completed",
            "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
            "config_source": cfg.config_source,
            "summary": result["summary"],
            "klines_count": len(klines),
        }
        proto.write_manifest(manifest, self.analysis_id, self.symbol, self.interval)

        log.info(f'[分析] 完成 analysis_id={self.analysis_id} → {result["summary"]}')
        return result["summary"]
