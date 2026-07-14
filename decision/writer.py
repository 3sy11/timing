"""DecisionWriter — decisions Parquet + manifest 写入。"""
import os, json, logging
from datetime import datetime, timezone
import duckdb
import pandas as pd

log = logging.getLogger(__name__)


class DecisionWriter:
    def __init__(self, warehouse: str, decision_id: str):
        self.base_dir = os.path.join(warehouse, "decisions", decision_id)
        self.decision_id = decision_id
        os.makedirs(self.base_dir, exist_ok=True)

    def write_decisions(self, decisions: list[dict]) -> str:
        path = os.path.join(self.base_dir, "decisions.parquet")
        df = pd.DataFrame(decisions) if decisions else pd.DataFrame(
            columns=["decision_id", "analysis_id", "symbol", "ts", "direction",
                     "strength", "price", "action", "side", "quantity", "reason"])
        if df.empty:
            df.to_parquet(path, index=False)
        else:
            with duckdb.connect() as conn:
                conn.execute("CREATE TEMP TABLE _buf AS SELECT * FROM df")
                conn.execute(f"COPY _buf TO '{path}' (FORMAT PARQUET)")
        log.info(f'[决策] 写入 decisions → {path} ({len(decisions)} 条)')
        return path

    def write_manifest(self, strategy: str, analysis_id: str, config: dict,
                       summary: dict, trace: dict = None) -> str:
        manifest = {
            "decision_id": self.decision_id, "strategy": strategy,
            "analysis_id": analysis_id,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "config": config, "summary": summary,
            "trace": trace or {},
        }
        path = os.path.join(self.base_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log.info(f'[决策] 写入 manifest → {path}')
        return path
