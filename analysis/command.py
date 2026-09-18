"""Analyze command — 读 result + line_events，写出 candidates。"""
import logging
from datetime import datetime, timezone
from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Analyze(BaseCommand):
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

        klines_df = proto.read_klines_df(self.symbol, self.interval)
        if klines_df.empty:
            log.error(f'[分析] 无 klines: {self.symbol}/{self.interval}')
            return None
        result_df = proto.read_result(upstream_algo, self.compute_id, self.symbol, self.interval)
        if result_df.empty:
            log.error(f'[分析] 无 result: {upstream_algo}/{self.compute_id}')
            return None
        events_df = proto.read_line_events(upstream_algo, self.compute_id, self.symbol, self.interval)

        log.info(f'[分析] 开始 rule={self.rule} analysis_id={self.analysis_id} klines={len(klines_df)} lines={len(result_df)}')
        result = detect_fn(
            klines_df, result_df, events_df, cfg,
            compute_id=self.compute_id, analysis_id=self.analysis_id,
        )
        proto.write_candidates(result["candidates"], self.analysis_id, self.symbol, self.interval)
        manifest = {
            "analysis_id": self.analysis_id, "rule": self.rule,
            "upstream_algo": upstream_algo, "compute_id": self.compute_id,
            "symbol": self.symbol, "interval": self.interval,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "completed",
            "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
            "config_source": cfg.config_source, "summary": result["summary"],
        }
        proto.write_manifest(manifest, self.analysis_id, self.symbol, self.interval)
        log.info(f'[分析] 完成 analysis_id={self.analysis_id} → {result["summary"]}')
        return result["summary"]
