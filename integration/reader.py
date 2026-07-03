"""读取外部数据文件（parquet/csv）→ 标准化 OHLCV list[dict]。

支持 OHLCV.meta 列名映射，支持目录/单文件两种输入。
"""
import csv, logging, os
from typing import Dict, List
import duckdb
import pandas as pd

log = logging.getLogger(__name__)


def _read_meta(directory: str) -> Dict[str, str]:
    p = os.path.join(directory, "OHLCV.meta")
    if not os.path.isfile(p): return {}
    m = {}
    with open(p, "r") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip() and row[1].strip(): m[row[0].strip()] = row[1].strip()
    return m


def _resolve_source(path: str):
    if os.path.isdir(path):
        for ext, fn in ((".parquet", "read_parquet"), (".csv", "read_csv_auto")):
            if any(f.endswith(ext) for f in os.listdir(path)):
                return path, os.path.join(path, f"*{ext}"), fn
        raise FileNotFoundError(f"no parquet/csv in {path}")
    return os.path.dirname(path), path, ("read_csv_auto" if path.endswith(".csv") else "read_parquet")


def _ts_to_ms(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        n = pd.to_numeric(s, errors="coerce").astype("float64")
        return (n.where(n > 1e12, n * 1000)).astype("int64")
    t = pd.to_datetime(s, errors="coerce").astype("datetime64[ns]")
    return (t.astype("int64") // 1_000_000).astype("int64")


def _apply_meta(df: pd.DataFrame, meta: Dict[str, str]) -> pd.DataFrame:
    if "ts" not in meta: raise ValueError("OHLCV.meta must map 'ts'")
    for std, raw in meta.items():
        if raw not in df.columns: raise ValueError(f"OHLCV.meta: column '{raw}' not in file ({list(df.columns)})")
    order = list(meta.keys())
    out = df[[meta[k] for k in order]].rename(columns={meta[k]: k for k in order})
    return out


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower = {str(c).strip().lower(): c for c in df.columns}
    need = ("ts", "open", "high", "low", "close")
    miss = [x for x in need if x not in lower]
    if miss: raise ValueError(f"parquet 缺少列 {miss}，请在目录下放 OHLCV.meta 映射")
    out = pd.DataFrame({k: df[lower[k]] for k in need})
    if "volume" in lower: out["volume"] = df[lower["volume"]]
    return out


def read_file(path: str) -> List[dict]:
    """duckdb 读文件 → 标准化 OHLCV → list[dict]。"""
    data_dir, glob, reader = _resolve_source(path)
    meta = _read_meta(data_dir)
    sql = f"SELECT * FROM {reader}('{glob}')"
    with duckdb.connect() as conn:
        df = conn.sql(sql).df()
    log.info(f"[integration] read {glob} rows={len(df)} meta={bool(meta)}")
    if meta: df = _apply_meta(df, meta)
    else: df = _normalize_columns(df)
    df["ts"] = _ts_to_ms(df["ts"])
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "", regex=False), errors="coerce").astype("float64")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"].astype(str).str.replace(",", "", regex=False), errors="coerce").fillna(0.0).astype("float64")
    else:
        df["volume"] = 0.0
    df = df.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)
    return df.to_dict("records")
