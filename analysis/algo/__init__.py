"""algo 子包：swing / fib / detector。提供 duckdb COPY TO CSV 工具函数。"""
import math, os, logging
log = logging.getLogger(__name__)


def dump_csv(path: str, header: list, rows: list):
    """duckdb COPY TO CSV 写中间结果。ts→BIGINT，str→VARCHAR，其余→DOUBLE，NaN→NULL。"""
    if not rows: return
    import duckdb
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    sample = rows[0]
    types = []
    for i, v in enumerate(sample):
        if header[i] == "ts": types.append("BIGINT")
        elif isinstance(v, str): types.append("VARCHAR")
        elif isinstance(v, int) and not isinstance(v, bool): types.append("BIGINT")
        else: types.append("DOUBLE")
    clean = [tuple(None if (isinstance(v, float) and math.isnan(v)) else v for v in r) for r in rows]
    conn = duckdb.connect()
    try:
        cols_def = ", ".join(f'"{h}" {t}' for h, t in zip(header, types))
        conn.execute(f"CREATE TABLE _d ({cols_def})")
        conn.executemany(f"INSERT INTO _d VALUES ({','.join(['?'] * len(header))})", clean)
        conn.execute(f"COPY _d TO '{path}' (FORMAT CSV, HEADER)")
        log.info(f'[CSV] {os.path.basename(path)} {len(rows)} rows')
    finally:
        conn.close()
