"""FeatureProtocol — 读计算事实，写特征宽表。"""
import os
import json
import logging
from typing import List

import duckdb
import pandas as pd
from bollydog.models.protocol import Protocol

from feature.jobs.price_touch.detect import CANDIDATE_COLS

log = logging.getLogger(__name__)


class FeatureProtocol(Protocol):

    def __init__(self, warehouse_path: str = "warehouse/timing", **kwargs):
        self.warehouse_path = warehouse_path
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        log.info(f'[特征Protocol] warehouse={self.warehouse_path}')

    def _compute_dir(self, algo: str, compute_id: str, symbol: str, interval: str) -> str:
        return os.path.join(self.warehouse_path, "computation", algo, compute_id, symbol, interval)

    def _feature_dir(self, feature_id: str, symbol: str, interval: str) -> str:
        path = os.path.join(self.warehouse_path, "features", feature_id, symbol, interval)
        os.makedirs(path, exist_ok=True)
        return path

    def read_klines_df(self, symbol: str, interval: str) -> pd.DataFrame:
        klines_dir = os.path.join(self.warehouse_path, "klines", symbol, interval)
        if not os.path.isdir(klines_dir):
            return pd.DataFrame()
        pattern = os.path.join(klines_dir, "*.parquet")
        with duckdb.connect() as conn:
            return conn.execute(
                f"SELECT DISTINCT ON (ts) * FROM read_parquet('{pattern}') ORDER BY ts"
            ).fetchdf()

    def read_result(self, algo: str, compute_id: str, symbol: str, interval: str) -> pd.DataFrame:
        path = os.path.join(self._compute_dir(algo, compute_id, symbol, interval), "result.parquet")
        if not os.path.isfile(path):
            log.warning(f'[特征] result 不存在: {path}')
            return pd.DataFrame()
        with duckdb.connect() as conn:
            return conn.execute(f"SELECT * FROM read_parquet('{path}')").fetchdf()

    def read_line_events(self, algo: str, compute_id: str, symbol: str, interval: str) -> pd.DataFrame:
        path = os.path.join(self._compute_dir(algo, compute_id, symbol, interval), "line_events.parquet")
        if not os.path.isfile(path):
            log.warning(f'[特征] line_events 不存在: {path}')
            return pd.DataFrame()
        with duckdb.connect() as conn:
            return conn.execute(f"SELECT * FROM read_parquet('{path}')").fetchdf()

    def write_candidates(self, rows: List[dict], feature_id: str,
                         symbol: str, interval: str) -> str:
        base_dir = self._feature_dir(feature_id, symbol, interval)
        path = os.path.join(base_dir, "candidates.parquet")
        df = pd.DataFrame(rows, columns=CANDIDATE_COLS) if rows else pd.DataFrame(columns=CANDIDATE_COLS)
        df.to_parquet(path, index=False)
        log.info(f'[特征] 写入候选 → {path} ({len(rows)} 条)')
        return path

    def write_manifest(self, manifest_data: dict, feature_id: str,
                       symbol: str, interval: str) -> str:
        base_dir = self._feature_dir(feature_id, symbol, interval)
        path = os.path.join(base_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)
        log.info(f'[特征] 写入 manifest → {path}')
        return path
