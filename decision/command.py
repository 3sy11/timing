"""Decide command — 决策模块 CLI 入口。"""
import logging
from typing import Any, ClassVar
import duckdb
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from .strategies import STRATEGY_REGISTRY
from .runner import run_strategy
from .writer import DecisionWriter

log = logging.getLogger(__name__)


class Decide(BaseCommand):
    destination: ClassVar[str] = "decision.DecisionService.Decide"
    strategy: str = ""
    decision_id: str = ""
    analysis_id: str = ""
    symbol: str = ""
    interval: str = ""
    min_strength: float = 0.6
    position_size: float = 0.1

    async def __call__(self, *args, **kwargs) -> Any:
        if not (self.strategy and self.decision_id and self.analysis_id):
            log.error('[决策] Decide 缺少必要参数: strategy, decision_id, analysis_id')
            return None
        strategy_meta = STRATEGY_REGISTRY.get(self.strategy)
        if not strategy_meta:
            log.error(f'[决策] 未知策略: {self.strategy}, 可用: {list(STRATEGY_REGISTRY.keys())}')
            return None
        warehouse = app.warehouse_path
        signals = self._load_signals(warehouse)
        if not signals:
            log.error(f'[决策] 无信号数据: analysis_id={self.analysis_id}')
            return None
        cfg = {"min_strength": self.min_strength, "position_size": self.position_size}
        log.info(f'[决策] 开始 strategy={self.strategy} decision_id={self.decision_id} '
                 f'signals={len(signals)} cfg={cfg}')
        decisions = run_strategy(signals, strategy_meta, cfg,
                                 decision_id=self.decision_id, analysis_id=self.analysis_id)
        writer = DecisionWriter(warehouse=warehouse, decision_id=self.decision_id)
        writer.write_decisions(decisions)
        summary = {"total": len(decisions),
                   "submit": sum(1 for d in decisions if d["action"] == "submit"),
                   "skip": sum(1 for d in decisions if d["action"] == "skip")}
        trace = self._build_trace(warehouse)
        writer.write_manifest(strategy=self.strategy, analysis_id=self.analysis_id,
                              config=cfg, summary=summary, trace=trace)
        log.info(f'[决策] 完成 decision_id={self.decision_id} → {summary}')
        return summary

    def _load_signals(self, warehouse: str) -> list[dict]:
        import glob as g
        pattern = f"{warehouse}/signals/{self.analysis_id}/**/*.parquet"
        files = g.glob(pattern, recursive=True)
        if not files:
            pattern2 = f"{warehouse}/signals/{self.analysis_id}/*.parquet"
            files = g.glob(pattern2)
        if not files:
            return []
        read_path = f"{warehouse}/signals/{self.analysis_id}/**/*.parquet"
        try:
            with duckdb.connect() as conn:
                sql = f"SELECT * FROM read_parquet('{read_path}', union_by_name=true)"
                if self.symbol:
                    sql += f" WHERE symbol = '{self.symbol}'"
                if self.interval:
                    sql += f" {'AND' if self.symbol else 'WHERE'} interval = '{self.interval}'"
                sql += " ORDER BY ts"
                return conn.execute(sql).fetchdf().to_dict("records")
        except Exception as e:
            log.error(f'[决策] 读取 signals 失败: {e}')
            return []

    def _build_trace(self, warehouse: str) -> dict:
        """尝试从 signals manifest 获取上游追溯信息。"""
        import glob as g, json
        manifests = g.glob(f"{warehouse}/signals/{self.analysis_id}/**/manifest.json", recursive=True)
        if not manifests:
            manifests = g.glob(f"{warehouse}/signals/{self.analysis_id}/manifest.json")
        if manifests:
            try:
                with open(manifests[0]) as f:
                    m = json.load(f)
                return {"analysis_id": self.analysis_id,
                        "compute_id": m.get("compute_id", ""),
                        "algo": m.get("upstream_algo", m.get("algo", ""))}
            except Exception:
                pass
        return {"analysis_id": self.analysis_id}
