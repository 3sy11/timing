"""Analyze command — 读 candidates + result，按规则写出 signals。"""
import logging
from datetime import datetime, timezone
from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Analyze(BaseCommand):
    destination: ClassVar[str] = "analysis.AnalysisService.Analyze"
    rule: str = ""
    feature_id: str = ""
    signal_id: str = ""
    symbol: str = ""
    interval: str = ""
    profile: str = ""
    override: str = ""

    async def __call__(self) -> dict | None:
        if not (self.rule and self.feature_id and self.signal_id and self.symbol and self.interval):
            log.error('[分析] Analyze 缺少必要参数: rule, feature_id, signal_id, symbol, interval')
            return None

        try:
            rule_meta = app.get_rule(self.rule)
        except (KeyError, AttributeError):
            log.error(f'[分析] 无规则 {self.rule!r}')
            return None

        config_class = rule_meta["config_class"]
        detect_fn = rule_meta["detect_fn"]
        upstream_algo = rule_meta["upstream_algo"]
        profile_name = self.profile or "default"
        override_list = [s.strip() for s in self.override.split(",") if s.strip()] if self.override else []
        cfg = config_class.from_profile(profile_name, override_list)
        proto = app.protocol

        candidates = proto.read_candidates(self.feature_id, self.symbol, self.interval)
        if candidates.empty:
            log.error(f'[分析] 无特征宽表: features/{self.feature_id}/{self.symbol}/{self.interval}')
            return None
        compute_ids = [c for c in candidates["compute_id"].dropna().unique().tolist()]
        if len(compute_ids) != 1:
            log.error(f'[分析] candidates.compute_id 不唯一: {compute_ids}')
            return None
        result_df = proto.read_result(upstream_algo, str(compute_ids[0]), self.symbol, self.interval)
        if result_df.empty:
            log.error(f'[分析] 无 result: {upstream_algo}/{compute_ids[0]}')
            return None

        log.info(f'[分析] 开始 rule={self.rule} signal_id={self.signal_id} candidates={len(candidates)}')
        result = detect_fn(
            candidates, result_df, cfg,
            signal_id=self.signal_id, symbol=self.symbol, interval=self.interval,
        )
        proto.write_signals(result["signals"], self.signal_id, self.symbol, self.interval)
        proto.write_manifest({
            "signal_id": self.signal_id,
            "rule": self.rule,
            "upstream_algo": upstream_algo,
            "compute_id": str(compute_ids[0]),
            "feature_id": self.feature_id,
            "symbol": self.symbol,
            "interval": self.interval,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "completed",
            "config": {k: v for k, v in cfg.items() if not str(k).startswith("_")},
            "config_source": cfg.config_source,
            "summary": result["summary"],
        }, self.signal_id, self.symbol, self.interval)
        log.info(f'[分析] 完成 signal_id={self.signal_id} → {result["summary"]}')
        return result["summary"]
