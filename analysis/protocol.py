"""AnalysisProtocol — 封装分析模块的所有 Parquet IO。

读上游结构表 + 读 klines + 写 signals + 写 manifest。
v3: 新增 read_structures_v3 / build_history_resolver 支持 PriceLine + FibLevel。
"""
import os
import json
import logging
from bisect import bisect_right
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Callable

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

    # ═══ v3: PriceLine + FibLevel 结构读取 ═══

    def read_structures_v3(self, algo: str, compute_id: str, symbol: str, interval: str) -> dict:
        """实时模式: 读 result.parquet 最新记录, 返回 {mult: snapshot}。"""
        path = os.path.join(self.warehouse_path, "computation", algo,
                            compute_id, symbol, interval, "result.parquet")
        if not os.path.isfile(path):
            log.warning(f'[分析v3] 结构文件不存在: {path}')
            return {}
        with duckdb.connect() as conn:
            rows = conn.execute(
                f"SELECT multiplier, fib_quality, is_valid, levels_json, price_lines_json "
                f"FROM read_parquet('{path}')"
            ).fetchall()
        result = {}
        for mult, fib_quality, is_valid, levels_json, price_lines_json in rows:
            price_lines = json.loads(price_lines_json) if price_lines_json else []
            fib_levels = json.loads(levels_json) if levels_json else []
            result[int(mult)] = {"price_lines": price_lines, "fib_levels": fib_levels,
                                 "fib_quality": float(fib_quality) if fib_quality else 0.0,
                                 "is_valid": bool(is_valid)}
        return result

    def build_history_resolver(self, algo: str, compute_id: str,
                               symbol: str, interval: str) -> Callable:
        """回测模式: 读 fib_history.parquet, 构建时间回溯 resolver(bar_ts)。
        从 anchored FibLevel 自动重建 PriceLine (无需依赖 step3_price_lines)。
        """
        path = os.path.join(self.warehouse_path, "computation", algo,
                            compute_id, symbol, interval, "fib_history.parquet")
        if not os.path.isfile(path):
            log.warning(f'[分析v3] fib_history 不存在: {path}')
            return lambda ts: {}
        with duckdb.connect() as conn:
            hist_df = conn.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY compute_ts, multiplier, ratio").fetchdf()
        snapshots_by_ts = {}
        for compute_ts, grp in hist_df.groupby("compute_ts"):
            ct = int(compute_ts)
            by_mult = {}
            for mult, mgrp in grp.groupby("multiplier"):
                m = int(mult)
                fib_quality = float(mgrp["fib_quality"].iloc[0])
                is_valid = bool(mgrp["is_valid"].iloc[0])
                leg_high = float(mgrp["leg_high"].iloc[0])
                leg_low = float(mgrp["leg_low"].iloc[0])
                leg_range = leg_high - leg_low
                fib_levels = []
                price_lines = []
                for _, r in mgrp.iterrows():
                    fl = {"ratio": float(r["ratio"]), "price": float(r["price"]),
                          "is_anchored": bool(r["is_anchored"]),
                          "anchor_center": float(r["anchor_center"]) if pd.notna(r["anchor_center"]) else None,
                          "anchor_strength": float(r["anchor_strength"])}
                    fib_levels.append(fl)
                    if fl["is_anchored"] and fl["anchor_center"] is not None:
                        ratio = fl["ratio"]
                        price_lines.append({
                            "center": fl["anchor_center"],
                            "tolerance": leg_range * 0.005 if leg_range > 0 else 1.0,
                            "line_strength": fl["anchor_strength"],
                            "hit_count": 1,
                            "is_bidirectional": False,
                            "has_high": ratio > 0.5,
                            "has_low": ratio <= 0.5,
                        })
                by_mult[m] = {"price_lines": price_lines, "fib_levels": fib_levels,
                             "fib_quality": fib_quality, "is_valid": is_valid}
            snapshots_by_ts[ct] = by_mult
        sorted_cts = sorted(snapshots_by_ts.keys())
        log.info(f'[分析v3] 构建 history resolver: {len(sorted_cts)} 个快照点, 从 anchored FibLevel 重建 PriceLine')

        def resolver(bar_ts: int) -> dict:
            idx = bisect_right(sorted_cts, bar_ts) - 1
            if idx < 0:
                return {}
            return snapshots_by_ts[sorted_cts[idx]]
        return resolver

    # ═══ 旧接口 (deprecated, fib_touch 兼容) ═══

    def read_structures_timeseries(self, algo: str, compute_id: str,
                                   symbol: str, interval: str):
        path = os.path.join(self.warehouse_path, "computation", algo,
                            compute_id, symbol, interval, "result.parquet")
        if not os.path.isfile(path):
            return [], {}, {}
        with duckdb.connect() as conn:
            rows = conn.execute(
                f"SELECT effective_ts, multiplier, direction, fib_quality as score, "
                f"effective_ts as leg_start_ts, effective_ts as leg_end_ts, "
                f"leg_low, leg_high, levels_json, invalidated_ts, invalidate_reason "
                f"FROM read_parquet('{path}') ORDER BY effective_ts, multiplier"
            ).fetchall()
        ts_groups: Dict[int, List[FibGroup]] = {}
        invalids: Dict[Tuple[int, int, str], int] = {}
        for row in rows:
            eff_ts, mult, direction, score, start_ts, end_ts, low, high, levels_json, inv_ts, inv_reason = row
            eff_ts = int(eff_ts)
            leg = TrendLeg(start_idx=0, end_idx=0, start_ts=int(start_ts or eff_ts),
                           end_ts=int(end_ts or eff_ts), low=float(low), high=float(high),
                           direction=direction or "up")
            raw_levels = json.loads(levels_json) if levels_json else []
            levels = [(float(lv["ratio"]), float(lv["price"])) for lv in raw_levels] if raw_levels and isinstance(raw_levels[0], dict) else [(float(r), float(p)) for r, p in raw_levels]
            g = FibGroup(leg=leg, levels=levels, score=float(score or 0),
                         direction=direction or "up", multiplier=int(mult))
            ts_groups.setdefault(eff_ts, []).append(g)
            if inv_ts is not None:
                invalids[(eff_ts, int(mult), direction or "up")] = int(inv_ts)
        sorted_ts = sorted(ts_groups.keys())
        return sorted_ts, ts_groups, invalids

    def get_groups_at(self, sorted_ts: List[int], ts_groups: Dict[int, List[FibGroup]],
                      invalids: Dict, bar_ts: int) -> List[FibGroup]:
        result = []
        for ets in sorted_ts:
            if ets > bar_ts:
                break
            for g in ts_groups[ets]:
                inv_key = (ets, g.multiplier, g.direction)
                inv_ts = invalids.get(inv_key)
                if inv_ts is not None and inv_ts <= bar_ts:
                    continue
                result.append(g)
        best = {}
        for g in result:
            key = (g.multiplier, g.direction)
            if key not in best or g.score > best[key].score:
                best[key] = g
        return list(best.values())

    def read_structures(self, algo: str, compute_id: str,
                        symbol: str, interval: str) -> List[FibGroup]:
        sorted_ts, ts_groups, _ = self.read_structures_timeseries(algo, compute_id, symbol, interval)
        if not sorted_ts:
            return []
        return ts_groups[sorted_ts[-1]]

    # ═══ 公共 IO ═══

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

    def read_klines_df(self, symbol: str, interval: str) -> pd.DataFrame:
        klines_dir = os.path.join(self.warehouse_path, "klines", symbol, interval)
        if not os.path.isdir(klines_dir):
            return pd.DataFrame()
        pattern = os.path.join(klines_dir, "*.parquet")
        with duckdb.connect() as conn:
            return conn.execute(f"SELECT DISTINCT ON (ts) * FROM read_parquet('{pattern}') ORDER BY ts").fetchdf()

    def write_signals(self, signals: List[dict], analysis_id: str,
                      symbol: str, interval: str) -> str:
        base_dir = os.path.join(self.warehouse_path, "signals", analysis_id, symbol, interval)
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, "signals.parquet")
        if not signals:
            pd.DataFrame(columns=["ts", "symbol", "compute_id", "direction", "strength",
                                   "price", "level", "multiplier"]
                         ).to_parquet(path, index=False)
        else:
            df = pd.DataFrame(signals)
            df.to_parquet(path, index=False)
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
