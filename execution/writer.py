"""ExecutionWriter — orders/fills/positions Parquet + manifest 写入。

输出路径: warehouse/execution/{execution_id}/
  ├── orders.parquet
  ├── fills.parquet
  ├── positions.parquet
  └── manifest.json
"""
import os, json, logging
from datetime import datetime, timezone
import duckdb
import pandas as pd

log = logging.getLogger(__name__)


class ExecutionWriter:
    def __init__(self, warehouse: str, execution_id: str):
        self.base_dir = os.path.join(warehouse, "execution", execution_id)
        self.execution_id = execution_id
        os.makedirs(self.base_dir, exist_ok=True)

    def write_orders(self, orders: list[dict]) -> str:
        return self._write_parquet(orders, "orders.parquet",
                                   ["order_id", "execution_id", "symbol", "side", "quantity",
                                    "order_type", "price", "status", "ts", "decision_id"])

    def write_fills(self, fills: list[dict]) -> str:
        return self._write_parquet(fills, "fills.parquet",
                                   ["order_id", "execution_id", "symbol", "side",
                                    "filled_price", "filled_quantity", "commission", "ts"])

    def write_positions(self, positions: list[dict]) -> str:
        return self._write_parquet(positions, "positions.parquet",
                                   ["execution_id", "symbol", "ts", "side", "quantity",
                                    "avg_price", "realized_pnl"])

    def write_manifest(self, decision_id: str, exchange_name: str, config: dict,
                       summary: dict, trace: dict = None) -> str:
        manifest = {
            "execution_id": self.execution_id, "decision_id": decision_id,
            "exchange": exchange_name,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "config": config, "summary": summary, "trace": trace or {},
        }
        path = os.path.join(self.base_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log.info(f'[执行] 写入 manifest → {path}')
        return path

    def _write_parquet(self, data: list[dict], filename: str, columns: list[str]) -> str:
        path = os.path.join(self.base_dir, filename)
        df = pd.DataFrame(data) if data else pd.DataFrame(columns=columns)
        if df.empty:
            df.to_parquet(path, index=False)
        else:
            with duckdb.connect() as conn:
                conn.execute("CREATE TEMP TABLE _buf AS SELECT * FROM df")
                conn.execute(f"COPY _buf TO '{path}' (FORMAT PARQUET)")
        log.info(f'[执行] 写入 {filename} → {path} ({len(data)} 条)')
        return path
