"""duckdb 读取 Parquet → List[dict]；同一 ts 多行时保留最后一行。
支持 OHLCV.meta CSV 映射文件：第一列 Kline 标准字段，第二列 parquet 实际列名。
Jupyter:
    from timing.data.clients.file import read_klines
    klines = read_klines("/path/to/parquet_dir")
"""
import csv, os
from datetime import datetime
from typing import Any, ClassVar, Dict, List
import duckdb
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService

KLINE_FIELDS = ("ts", "open", "high", "low", "close", "volume")


def _dedupe_by_ts(rows: List[dict]) -> List[dict]:
    by_ts: Dict[int, dict] = {}
    for r in rows: by_ts[int(r["ts"])] = r
    return [by_ts[t] for t in sorted(by_ts)]


def _read_meta(directory: str) -> Dict[str, str]:
    meta_path = os.path.join(directory, "OHLCV.meta")
    if not os.path.isfile(meta_path): return {}
    mapping = {}
    with open(meta_path, "r") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                mapping[row[0].strip()] = row[1].strip()
    return mapping


def _to_ts_ms(val) -> int:
    if val is None: raise ValueError("ts value is None")
    if hasattr(val, "timestamp"): return int(val.timestamp() * 1000)
    if isinstance(val, str):
        val = val.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
            try: return int(datetime.strptime(val, fmt).timestamp() * 1000)
            except ValueError: continue
        try:
            x = float(val.replace(",", ""))
            return int(x) if x > 1e12 else int(x * 1000)
        except ValueError: raise ValueError(f"Cannot convert '{val}' to timestamp")
    x = float(val)
    return int(x) if x > 1e12 else int(x * 1000)


def _to_float(val) -> float:
    if isinstance(val, str): return float(val.replace(",", ""))
    return float(val)


def read_klines(path: str) -> List[dict]:
    """纯函数：读取 parquet → List[dict]。直接 duckdb 读取，无 Service 依赖。"""
    if os.path.isdir(path):
        data_dir, path = path, os.path.join(path, "*.parquet")
    else:
        data_dir = os.path.dirname(path)
    meta = _read_meta(data_dir)
    conn = duckdb.connect()
    try:
        if meta:
            ts_src = meta.get("ts")
            if not ts_src: raise ValueError("OHLCV.meta must map 'ts'")
            selects = [f'"{meta[kf]}" as {kf}' for kf in KLINE_FIELDS if meta.get(kf)]
            sql = f"SELECT {', '.join(selects)} FROM read_parquet('{path}') ORDER BY \"{ts_src}\""
        else:
            cols = {r[0].lower() for r in conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()}
            missing = [f for f in ("open", "high", "low", "close", "ts") if f not in cols]
            if missing: raise ValueError(f"parquet 缺少必需列: {missing}，请在数据目录创建 OHLCV.meta 映射文件")
            selects = [f'"{f}" as {f}' for f in KLINE_FIELDS if f in cols]
            sql = f"SELECT {', '.join(selects)} FROM read_parquet('{path}') ORDER BY \"ts\""
        rows = conn.execute(sql).fetchall()
        names = [d[0] for d in conn.execute(f"{sql} LIMIT 0").description]
    finally:
        conn.close()
    out: List[dict] = []
    has_vol = "volume" in names
    ti = names.index("ts")
    for r in rows:
        out.append({"open": _to_float(r[names.index("open")]), "high": _to_float(r[names.index("high")]),
                     "low": _to_float(r[names.index("low")]), "close": _to_float(r[names.index("close")]),
                     "ts": _to_ts_ms(r[ti]), "volume": _to_float(r[names.index("volume")]) if has_vol else 0.0})
    return _dedupe_by_ts(out)


class ReadParquetKlines(BaseCommand):
    """读取 Parquet → 标准 Kline list[dict]。纯计算，无 protocol。
    Jupyter: cmd = ReadParquetKlines(path="..."); result = await cmd()"""
    destination: ClassVar[str] = "timing.DataEngine.ReadParquetKlines"
    path: str = ""
    async def __call__(self, *args, **kwargs) -> Any:
        return {"klines": read_klines(self.path)}


class FileDataClient(AppService):
    """Parquet 文件数据源，生命周期管理。"""
    domain = "timing"
    alias = "FileDataClient"
    commands = ["timing.data.clients.file"]
