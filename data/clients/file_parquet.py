"""Parquet 读入 Kline。"""
from typing import Dict, List

import pyarrow.parquet as pq

from timing.models.kline import Kline
from bollydog.models.service import AppService


def _norm_map(names: List[str]) -> Dict[str, str]:
    return {n.lower(): n for n in names}


class FileParquetDataClient(AppService):
    domain = "timing"
    alias = "FileParquetDataClient"

    @staticmethod
    def read_klines(path: str) -> List[Kline]:
        t = pq.read_table(path)
        cmap = _norm_map(list(t.column_names))

        def pick(*cands: str) -> str:
            for c in cands:
                if c.lower() in cmap:
                    return cmap[c.lower()]
            raise ValueError(f"parquet 缺少列，尝试过: {cands}")

        c_open = pick("open", "o")
        c_high = pick("high", "h")
        c_low = pick("low", "l")
        c_close = pick("close", "c")
        c_ts = pick("ts", "timestamp", "time")
        c_vol = cmap.get("volume") or cmap.get("vol") or cmap.get("v")
        n = t.num_rows
        o = t.column(c_open).to_pylist()
        h = t.column(c_high).to_pylist()
        lo = t.column(c_low).to_pylist()
        cl = t.column(c_close).to_pylist()
        ts = t.column(c_ts).to_pylist()
        vo = t.column(c_vol).to_pylist() if c_vol else [0.0] * n
        out: List[Kline] = []
        for i in range(n):
            tv = ts[i]
            if hasattr(tv, "timestamp"):
                ts_ms = int(tv.timestamp() * 1000)
            else:
                x = float(tv)
                ts_ms = int(x) if x > 1e12 else int(x * 1000)
            out.append(
                Kline(
                    open=float(o[i]),
                    high=float(h[i]),
                    low=float(lo[i]),
                    close=float(cl[i]),
                    volume=float(vo[i] if i < len(vo) else 0),
                    ts=ts_ms,
                )
            )
        out.sort(key=lambda x: x.ts)
        return out
