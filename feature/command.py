"""Assemble command — 读 result + line_events，写出特征宽表。"""
import logging
from datetime import datetime, timezone
from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Assemble(BaseCommand):
    destination: ClassVar[str] = "feature.FeatureService.Assemble"
    job: str = ""
    compute_id: str = ""
    feature_id: str = ""
    symbol: str = ""
    interval: str = ""
    profile: str = ""
    override: str = ""

    async def __call__(self) -> dict | None:
        if not (self.job and self.compute_id and self.feature_id and self.symbol and self.interval):
            log.error('[特征] Assemble 缺少必要参数: job, compute_id, feature_id, symbol, interval')
            return None

        job_meta = app.get_job(self.job)
        config_class = job_meta["config_class"]
        build_fn = job_meta["build_fn"]
        upstream_algo = job_meta["upstream_algo"]

        profile_name = self.profile or "default"
        override_list = [s.strip() for s in self.override.split(",") if s.strip()] if self.override else []
        cfg = config_class.from_profile(profile_name, override_list)
        proto = app.protocol

        klines_df = proto.read_klines_df(self.symbol, self.interval)
        if klines_df.empty:
            log.error(f'[特征] 无 klines: {self.symbol}/{self.interval}')
            return None
        result_df = proto.read_result(upstream_algo, self.compute_id, self.symbol, self.interval)
        if result_df.empty:
            log.error(f'[特征] 无 result: {upstream_algo}/{self.compute_id}')
            return None
        events_df = proto.read_line_events(upstream_algo, self.compute_id, self.symbol, self.interval)

        log.info(
            f'[特征] 开始 job={self.job} feature_id={self.feature_id} '
            f'klines={len(klines_df)} lines={len(result_df)}'
        )
        result = build_fn(
            klines_df, result_df, events_df, cfg,
            compute_id=self.compute_id, feature_id=self.feature_id,
        )
        proto.write_candidates(result["candidates"], self.feature_id, self.symbol, self.interval)
        proto.write_manifest({
            "feature_id": self.feature_id,
            "job": self.job,
            "upstream_algo": upstream_algo,
            "compute_id": self.compute_id,
            "symbol": self.symbol,
            "interval": self.interval,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "completed",
            "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
            "config_source": cfg.config_source,
            "summary": result["summary"],
        }, self.feature_id, self.symbol, self.interval)
        log.info(f'[特征] 完成 feature_id={self.feature_id} → {result["summary"]}')
        return result["summary"]
