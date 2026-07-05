"""StepWriter — 计算模块内部 Parquet 写入工具 + 实验 manifest。"""
import os, json, logging
from datetime import datetime, timezone
import duckdb
import pandas as pd

log = logging.getLogger(__name__)


class StepWriter:
    """将 DataFrame 写入 computation/{algo}/{compute_id}/{symbol}/{interval}/ 下的 Parquet 文件。"""

    def __init__(self, warehouse: str, algo: str, compute_id: str, symbol: str, interval: str):
        self.base_dir = os.path.join(warehouse, "computation", algo, compute_id, symbol, interval)
        self.algo = algo
        self.compute_id = compute_id
        self.symbol = symbol
        self.interval = interval
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, f"{name}.parquet")

    def write_step(self, step_name: str, df: pd.DataFrame) -> str:
        path = self._path(step_name)
        self._write(df, path)
        log.info(f'[计算] 写入中间表 {step_name} → {path} ({len(df)}行)')
        return path

    def write_result(self, df: pd.DataFrame) -> str:
        path = self._path("result")
        self._write(df, path)
        log.info(f'[计算] 写入投产表 result → {path} ({len(df)}行)')
        return path

    def write_manifest(self, config: dict, klines_count: int,
                       result_summary: dict, config_source: dict = None) -> str:
        """写入 manifest.json — 记录实验完整配置和元数据。"""
        manifest = {
            "compute_id": self.compute_id,
            "algo": self.algo,
            "symbol": self.symbol,
            "interval": self.interval,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "completed",
            "source": {
                "klines_path": f"klines/{self.symbol}/{self.interval}/*.parquet",
                "klines_count": klines_count,
            },
            "config": config,
            "config_source": config_source or {},
            "result_summary": result_summary,
        }
        path = os.path.join(self.base_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log.info(f'[计算] 写入 manifest → {path}')
        return path

    def _write(self, df: pd.DataFrame, path: str):
        if df.empty:
            df.to_parquet(path, index=False)
            return
        with duckdb.connect() as conn:
            conn.execute("CREATE TEMP TABLE _buf AS SELECT * FROM df")
            conn.execute(f"COPY _buf TO '{path}' (FORMAT PARQUET)")
