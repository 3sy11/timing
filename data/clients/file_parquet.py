"""duckdb 读取 Parquet → List[dict]；同一 ts 多行时保留最后一行（不改源文件）。"""
from typing import Dict, List
import duckdb
from bollydog.models.service import AppService


def _dedupe_by_ts(rows: List[dict]) -> List[dict]:
    by_ts: Dict[int, dict] = {}
    for r in rows:
        ts = int(r["ts"])
        by_ts[ts] = r
    return [by_ts[t] for t in sorted(by_ts)]


class FileParquetDataClient(AppService):
    domain = "timing"
    alias = "FileParquetDataClient"

    @staticmethod
    def read_klines(path: str) -> List[dict]:
        conn = duckdb.connect()
        try:
            cols = [r[0].lower() for r in conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
            col_map = {c: c for c in cols}
            def pick(*cands):
                for c in cands:
                    if c.lower() in col_map:
                        return col_map[c.lower()]
                raise ValueError(f"parquet 缺少列，尝试过: {cands}")
            c_open, c_high, c_low, c_close = pick("open", "o"), pick("high", "h"), pick("low", "l"), pick("close", "c")
            c_ts = pick("ts", "timestamp", "time")
            c_vol = col_map.get("volume") or col_map.get("vol") or col_map.get("v")
            sel = f'"{c_open}" as open, "{c_high}" as high, "{c_low}" as low, "{c_close}" as close, "{c_ts}" as ts'
            if c_vol:
                sel += f', "{c_vol}" as volume'
            rows = conn.execute(f"SELECT {sel} FROM read_parquet('{path}') ORDER BY \"{c_ts}\"").fetchall()
            desc = conn.execute(f"SELECT {sel} FROM read_parquet('{path}') LIMIT 0").description
            names = [d[0] for d in desc]
        finally:
            conn.close()
        out: List[dict] = []
        has_vol = "volume" in names
        for r in rows:
            tv = r[names.index("ts")]
            if hasattr(tv, "timestamp"):
                ts_ms = int(tv.timestamp() * 1000)
            else:
                x = float(tv)
                ts_ms = int(x) if x > 1e12 else int(x * 1000)
            d = {"open": float(r[0]), "high": float(r[1]), "low": float(r[2]), "close": float(r[3]),
                 "ts": ts_ms, "volume": float(r[names.index("volume")]) if has_vol else 0.0}
            out.append(d)
        return _dedupe_by_ts(out)
