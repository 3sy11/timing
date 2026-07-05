"""reader — 计算模块数据读取，直接从 Parquet 文件读取 klines。"""
import os
import logging
from typing import List

import duckdb

log = logging.getLogger(__name__)


def read_klines(warehouse: str, symbol: str, interval: str,
                start_ts: int = None, end_ts: int = None) -> List[dict]:
    """从 warehouse/klines/{symbol}/{interval}/*.parquet 直接读取 klines。

    不依赖任何服务实例，仅依赖文件系统路径。
    """
    klines_dir = os.path.join(warehouse, "klines", symbol, interval)
    if not os.path.isdir(klines_dir):
        log.warning(f'[计算] klines 目录不存在: {klines_dir}')
        return []

    pattern = os.path.join(klines_dir, "*.parquet")
    where_parts: list[str] = []
    params: dict = {}
    if start_ts is not None:
        where_parts.append("ts >= $start_ts")
        params["start_ts"] = start_ts
    if end_ts is not None:
        where_parts.append("ts <= $end_ts")
        params["end_ts"] = end_ts

    where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = f"SELECT DISTINCT ON (ts) * FROM read_parquet('{pattern}'){where} ORDER BY ts"

    with duckdb.connect() as conn:
        result = conn.execute(sql, params).fetchall()
        cols = [desc[0] for desc in conn.description]

    klines = [dict(zip(cols, r)) for r in result]
    log.info(f'[计算] 读取 klines: {symbol}/{interval} → {len(klines)} 条')
    return klines
