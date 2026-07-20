"""AnalysisProtocol — 封装分析模块的所有 Parquet IO。

读上游结构表 + 读 klines + 写 signals + 写 manifest。
Protocol 子类，可被 mock 替换用于测试。
"""
import os
import json
import logging
from bisect import bisect_right
from datetime import datetime, timezone
from typing import List, Dict, Tuple

import duckdb
import pandas as pd
from bollydog.models.protocol import Protocol

from computation.algo.fib_retracement.models import TrendLeg, FibGroup

log = logging.getLogger(__name__)


class AnalysisProtocol(Protocol):

    def __init__(self, warehouse_path: str = "warehouse/timing", **kwargs):
        self.warehouse_path = warehouse_path
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        log.info(f'[分析Protocol] warehouse={self.warehouse_path}')

    def read_structures_timeseries(self, algo: str, compute_id: str,
                                   symbol: str, interval: str) -> Tuple[List[int], Dict[int, List[FibGroup]]]:
        """读取时间序列 result.parquet，返回 (sorted_ts_list, {effective_ts: [FibGroup...]})。"""
        path = os.path.join(self.warehouse_path, "computation", algo,
                            compute_id, symbol, interval, "result.parquet")
        if not os.path.isfile(path):
            log.warning(f'[分析] 结构文件不存在: {path}')
            return [], {}
        with duckdb.connect() as conn:
            rows = conn.execute(
                f"SELECT effective_ts, multiplier, direction, score, leg_start_ts, leg_end_ts, "
                f"leg_low, leg_high, levels_json FROM read_parquet('{path}') "
                f"ORDER BY effective_ts, multiplier, score DESC"
            ).fetchall()
        ts_groups: Dict[int, List[FibGroup]] = {}
        for row in rows:
            eff_ts, mult, direction, score, start_ts, end_ts, low, high, levels_json = row
            eff_ts = int(eff_ts)
            leg = TrendLeg(start_idx=0, end_idx=0, start_ts=int(start_ts),
                           end_ts=int(end_ts), low=float(low), high=float(high),
                           direction=direction)
            levels = [(float(r), float(p)) for r, p in json.loads(levels_json)]
            g = FibGroup(leg=leg, levels=levels, score=float(score),
                         direction=direction, multiplier=int(mult))
            ts_groups.setdefault(eff_ts, []).append(g)
        sorted_ts = sorted(ts_groups.keys())
        log.info(f'[分析] 读取结构时间序列: {len(sorted_ts)} 个时间点, {len(rows)} 条记录')
        return sorted_ts, ts_groups

    def get_groups_at(self, sorted_ts: List[int], ts_groups: Dict[int, List[FibGroup]],
                      bar_ts: int) -> List[FibGroup]:
        """根据 bar 时间戳找到对应的 fib groups（effective_ts <= bar_ts 的最新一组）。"""
        idx = bisect_right(sorted_ts, bar_ts) - 1
        if idx < 0:
            return []
        return ts_groups[sorted_ts[idx]]

    def read_structures(self, algo: str, compute_id: str,
                        symbol: str, interval: str) -> List[FibGroup]:
        """兼容旧接口：返回最新一组 groups。"""
        sorted_ts, ts_groups = self.read_structures_timeseries(algo, compute_id, symbol, interval)
        if not sorted_ts:
            return []
        return ts_groups[sorted_ts[-1]]

    def read_klines(self, symbol: str, interval: str) -> List[dict]:
        klines_dir = os.path.join(self.warehouse_path, "klines", symbol, interval)
        if not os.path.isdir(klines_dir):
            log.warning(f'[分析] klines 目录不存在: {klines_dir}')
            return []
        pattern = os.path.join(klines_dir, "*.parquet")
        with duckdb.connect() as conn:
            result = conn.execute(
                f"SELECT DISTINCT ON (ts) * FROM read_parquet('{pattern}') ORDER BY ts"
            ).fetchall()
            cols = [desc[0] for desc in conn.description]
        return [dict(zip(cols, r)) for r in result]

    def write_signals(self, signals: List[dict], analysis_id: str,
                      symbol: str, interval: str) -> str:
        base_dir = os.path.join(self.warehouse_path, "signals", analysis_id, symbol, interval)
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, "signals.parquet")
        if not signals:
            pd.DataFrame(columns=["ts", "direction", "strength", "price",
                                   "level", "ratio", "group_idx", "type"]
                         ).to_parquet(path, index=False)
        else:
            df = pd.DataFrame(signals)
            with duckdb.connect() as conn:
                conn.execute("CREATE TEMP TABLE _buf AS SELECT * FROM df")
                conn.execute(f"COPY _buf TO '{path}' (FORMAT PARQUET)")
        log.info(f'[分析] 写入信号 → {path} ({len(signals)} 条)')
        return path

    def write_manifest(self, manifest_data: dict, analysis_id: str,
                       symbol: str, interval: str) -> str:
        base_dir = os.path.join(self.warehouse_path, "signals", analysis_id, symbol, interval)
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)
        log.info(f'[分析] 写入 manifest → {path}')
        return path
