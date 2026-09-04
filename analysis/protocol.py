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
        """回测模式: 读 lines.parquet (优先) 或 fib_history.parquet, 构建时间回溯 resolver(bar_ts)。
        返回 resolver(bar_ts) → {mult: {price_lines, fib_levels, fib_quality, is_valid}}
        """
        base = os.path.join(self.warehouse_path, "computation", algo, compute_id, symbol, interval)
        lines_path = os.path.join(base, "lines.parquet")
        fib_path = os.path.join(base, "fib.parquet")
        if os.path.isfile(lines_path) and os.path.isfile(fib_path):
            return self._build_resolver_from_split(lines_path, fib_path)
        if os.path.isfile(lines_path):
            return self._build_resolver_from_lines(lines_path)
        hist_path = os.path.join(base, "fib_history.parquet")
        if os.path.isfile(hist_path):
            return self._build_resolver_from_history(hist_path)
        log.warning(f'[分析v3] 无数据文件: {base}')
        return lambda ts: {}

    def _build_resolver_from_split(self, lines_path: str, fib_path: str) -> Callable:
        """从拆分的 lines.parquet + fib.parquet 构建 resolver。"""
        with duckdb.connect() as conn:
            det_df = conn.execute(f"SELECT * FROM read_parquet('{lines_path}') ORDER BY compute_ts, multiplier").fetchdf()
            fib_df = conn.execute(f"SELECT * FROM read_parquet('{fib_path}') ORDER BY compute_ts, multiplier").fetchdf()
        snapshots_by_ts = {}
        for compute_ts, grp in det_df.groupby("compute_ts"):
            ct = int(compute_ts)
            by_mult = {}
            for mult, mgrp in grp.groupby("multiplier"):
                m = int(mult)
                price_lines = []
                for _, r in mgrp.iterrows():
                    price_lines.append({
                        "center": float(r["center"]), "tolerance": float(r["tolerance"]),
                        "line_strength": float(r["strength"]), "hit_count": int(r["hit_count"]),
                        "is_bidirectional": bool(r["is_bidirectional"]),
                        "has_high": bool(r["has_high"]), "has_low": bool(r["has_low"]),
                    })
                by_mult[m] = {"price_lines": price_lines, "fib_levels": [], "fib_quality": 0.0, "is_valid": False}
            snapshots_by_ts[ct] = by_mult
        # 叠加 fib 数据
        for compute_ts, grp in fib_df.groupby("compute_ts"):
            ct = int(compute_ts)
            if ct not in snapshots_by_ts:
                snapshots_by_ts[ct] = {}
            for mult, mgrp in grp.groupby("multiplier"):
                m = int(mult)
                fib_quality = float(mgrp["fib_quality"].iloc[0])
                fib_levels = []
                for _, r in mgrp.iterrows():
                    delta = float(r["anchor_center"]) if pd.notna(r["anchor_center"]) else None
                    actual_anchor = round(float(r["center"]) - delta, 2) if delta is not None else None
                    fib_levels.append({
                        "ratio": float(r["fib_ratio"]) if pd.notna(r["fib_ratio"]) else 0.0,
                        "price": float(r["center"]),
                        "is_anchored": r["type"] == "fib_anchored",
                        "anchor_center": actual_anchor,
                        "anchor_strength": float(r["strength"]),
                    })
                # fib 的 multiplier 是 rank, 挂到所有 detected mult 上
                for dm in list(snapshots_by_ts[ct].keys()):
                    snap = snapshots_by_ts[ct][dm]
                    if not snap["fib_levels"] or fib_quality > snap["fib_quality"]:
                        snap["fib_levels"] = fib_levels
                        snap["fib_quality"] = fib_quality
                        snap["is_valid"] = fib_quality >= 0.2
        sorted_cts = sorted(snapshots_by_ts.keys())
        log.info(f'[分析v3] resolver from lines+fib: {len(sorted_cts)} 快照')
        def resolver(bar_ts: int) -> dict:
            idx = bisect_right(sorted_cts, bar_ts) - 1
            return snapshots_by_ts[sorted_cts[idx]] if idx >= 0 else {}
        return resolver

    def _build_resolver_from_lines(self, path: str) -> Callable:
        """从统一 lines.parquet 构建 resolver。"""
        with duckdb.connect() as conn:
            df = conn.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY compute_ts, multiplier").fetchdf()
        snapshots_by_ts = {}
        for compute_ts, grp in df.groupby("compute_ts"):
            ct = int(compute_ts)
            by_mult = {}
            for mult, mgrp in grp.groupby("multiplier"):
                m = int(mult)
                detected = mgrp[mgrp["type"] == "detected"]
                fib_rows = mgrp[mgrp["type"].isin(["fib_anchored", "fib_inferred", "fib_extended"])]
                price_lines = []
                for _, r in detected.iterrows():
                    price_lines.append({
                        "center": float(r["center"]), "tolerance": float(r["tolerance"]),
                        "line_strength": float(r["strength"]), "hit_count": int(r["hit_count"]),
                        "is_bidirectional": bool(r["is_bidirectional"]),
                        "has_high": bool(r["has_high"]), "has_low": bool(r["has_low"]),
                    })
                fib_levels = []
                fib_quality = 0.0
                is_valid = False
                for _, r in fib_rows.iterrows():
                    fib_quality = float(r["fib_quality"])
                    is_valid = fib_quality >= 0.2
                    # anchor_center 存的是差值, 还原实际 detected center = fib_price - delta
                    delta = float(r["anchor_center"]) if pd.notna(r["anchor_center"]) else None
                    actual_anchor = round(float(r["center"]) - delta, 2) if delta is not None else None
                    fib_levels.append({
                        "ratio": float(r["fib_ratio"]) if pd.notna(r["fib_ratio"]) else 0.0,
                        "price": float(r["center"]),
                        "is_anchored": r["type"] == "fib_anchored",
                        "anchor_center": actual_anchor,
                        "anchor_strength": float(r["strength"]),
                    })
                by_mult[m] = {"price_lines": price_lines, "fib_levels": fib_levels,
                             "fib_quality": fib_quality, "is_valid": is_valid}
            snapshots_by_ts[ct] = by_mult
        sorted_cts = sorted(snapshots_by_ts.keys())
        log.info(f'[分析v3] resolver from lines.parquet: {len(sorted_cts)} 快照')
        def resolver(bar_ts: int) -> dict:
            idx = bisect_right(sorted_cts, bar_ts) - 1
            return snapshots_by_ts[sorted_cts[idx]] if idx >= 0 else {}
        return resolver

    def _build_resolver_from_history(self, path: str) -> Callable:
        """从旧 fib_history.parquet 构建 resolver (兼容)。"""
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
                fib_levels, price_lines = [], []
                for _, r in mgrp.iterrows():
                    fl = {"ratio": float(r["ratio"]), "price": float(r["price"]),
                          "is_anchored": bool(r["is_anchored"]),
                          "anchor_center": float(r["anchor_center"]) if pd.notna(r["anchor_center"]) else None,
                          "anchor_strength": float(r["anchor_strength"])}
                    fib_levels.append(fl)
                    if fl["is_anchored"] and fl["anchor_center"] is not None:
                        price_lines.append({
                            "center": fl["anchor_center"],
                            "tolerance": leg_range * 0.005 if leg_range > 0 else 1.0,
                            "line_strength": fl["anchor_strength"], "hit_count": 1,
                            "is_bidirectional": False,
                            "has_high": fl["ratio"] > 0.5, "has_low": fl["ratio"] <= 0.5,
                        })
                by_mult[m] = {"price_lines": price_lines, "fib_levels": fib_levels,
                             "fib_quality": fib_quality, "is_valid": is_valid}
            snapshots_by_ts[ct] = by_mult
        sorted_cts = sorted(snapshots_by_ts.keys())
        log.info(f'[分析v3] resolver from fib_history: {len(sorted_cts)} 快照')
        def resolver(bar_ts: int) -> dict:
            idx = bisect_right(sorted_cts, bar_ts) - 1
            return snapshots_by_ts[sorted_cts[idx]] if idx >= 0 else {}
        return resolver

    # ═══ 旧接口 (deprecated, fib_touch 兼容) ═══

    def read_structures_timeseries(self, algo: str, compute_id: str,
                                   symbol: str, interval: str):
        # 优先读 step3_fib_groups.parquet（含 levels_json + score），fallback 到 result.parquet
        base = os.path.join(self.warehouse_path, "computation", algo,
                            compute_id, symbol, interval)
        step3_path = os.path.join(base, "step3_fib_groups.parquet")
        result_path = os.path.join(base, "result.parquet")
        path = step3_path if os.path.isfile(step3_path) else result_path
        if not os.path.isfile(path):
            return [], {}, {}
        with duckdb.connect() as conn:
            rows = conn.execute(
                f"SELECT effective_ts, multiplier, direction, score, "
                f"leg_start_ts, leg_end_ts, "
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
