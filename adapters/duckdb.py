"""TimingDuckDBProtocol — YAML-Schema 驱动的 DuckDB 统一存储。

继承 bollydog DuckDBProtocol，内置 registry.yml 解析，提供:
  - 自动建表（on_start 时从 YAML 生成 DDL）
  - 类型化 CRUD: get/put/append/delete/all
  - indicators_sql() 指标预计算 SQL 生成
  - JSON 列自动编解码
"""
import json, yaml, os, logging
from bollydog.adapters.sqlalchemy import DuckDBProtocol as BaseDuckDBProtocol

log = logging.getLogger(__name__)
_TYPE_MAP = {"string": "VARCHAR", "number": "DOUBLE", "time": "BIGINT", "boolean": "BOOLEAN"}
_JSON_COLS = ("data", "metadata", "params")


class TimingDuckDBProtocol(BaseDuckDBProtocol):
    _shared: 'TimingDuckDBProtocol' = None

    def __init__(self, url: str, registry_path: str = None, **kwargs):
        super().__init__(url=url, **kwargs)
        path = registry_path or os.path.join(os.path.dirname(__file__), "..", "schema", "registry.yml")
        with open(path) as f:
            self._cubes = {c["name"]: c for c in yaml.safe_load(f)["cubes"]}

    @classmethod
    def shared(cls, url: str = None, registry_path: str = None) -> 'TimingDuckDBProtocol':
        if cls._shared is None:
            data_root = os.environ.get("TIMING_DATA_ROOT", "warehouse/timing")
            url = url or os.path.join(data_root, "timing.duckdb")
            cls._shared = cls(url=url, registry_path=registry_path)
        return cls._shared

    @classmethod
    def reset_shared(cls):
        cls._shared = None

    # ── schema 查询 ──

    def tables(self) -> list[str]:
        return list(self._cubes.keys())

    def columns(self, table: str) -> list[str]:
        return [d["name"] for d in self._cubes[table]["dimensions"]]

    def primary_key(self, table: str) -> list[str]:
        return self._cubes[table].get("x-storage", {}).get("primary_key", [])

    def ddl(self, table: str) -> str:
        c = self._cubes[table]
        cols = [f'"{d["name"]}" {_TYPE_MAP.get(d["type"], "VARCHAR")}' for d in c["dimensions"]]
        sql = f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(cols)}'
        pk = self.primary_key(table)
        if pk:
            sql += f', PRIMARY KEY ({", ".join(pk)})'
        return sql + ")"

    def indicators_sql(self, symbol: str, interval: str) -> str:
        compute = self._cubes["indicators"].get("x-storage", {}).get("compute", {})
        parts = ["symbol", '"interval"', "ts"]
        for col, spec in compute.get("window", {}).items():
            arg_str = ", ".join(str(a) for a in spec["args"])
            parts.append(f'{spec["fn"]}({arg_str}) OVER (PARTITION BY symbol ORDER BY ts) AS {col}')
        for _, spec in compute.get("multi_output", {}).items():
            for out in spec["outputs"]:
                parts.append(f'NULL AS {out}')
        return (f'INSERT OR REPLACE INTO indicators SELECT {", ".join(parts)} '
                f"FROM klines WHERE symbol='{symbol}' AND \"interval\"='{interval}'")

    # ── lifecycle ──

    async def on_start(self) -> None:
        os.makedirs(os.path.dirname(self.url) or ".", exist_ok=True)
        await super().on_start()
        try:
            self.adapter.execute("LOAD talib")
        except Exception:
            pass
        for t in self.tables():
            self.adapter.execute(self.ddl(t))
        log.info(f'[TimingDuckDB] ready: {self.url}, tables={self.tables()}')

    async def _run(self, fn, *args, **kwargs):
        """DuckDB 内嵌引擎直接同步执行，避免线程并发导致 'No open result set'。"""
        return fn(*args, **kwargs)

    # ── CRUD ──

    async def get(self, table: str = None, **where) -> dict | list[dict]:
        pk, cols = self.primary_key(table), self.columns(table)
        sql, params = f'SELECT * FROM {table}', []
        if where:
            conds = [f'"{k}"=?' for k in where]
            sql += ' WHERE ' + " AND ".join(conds)
            params = list(where.values())
        rows = [dict(zip(cols, r)) for r in
                await self._run(lambda: self.adapter.execute(sql, params).fetchall())]
        for row in rows:
            self._decode_json(row)
        if pk and set(where.keys()) >= set(pk):
            return rows[0] if rows else None
        return rows

    async def put(self, table: str, data: dict, **_):
        cols = self.columns(table)
        values = [self._encode(c, data.get(c)) for c in cols]
        col_str = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(["?"] * len(cols))
        await self._run(lambda: self.adapter.execute(
            f'INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({ph})', values))

    async def append(self, table: str, data):
        rows = data if isinstance(data, list) else [data]
        cols = self.columns(table)
        col_str = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(["?"] * len(cols))
        for row in rows:
            v = [self._encode(c, row.get(c)) for c in cols]
            await self._run(lambda v=v: self.adapter.execute(
                f'INSERT INTO {table} ({col_str}) VALUES ({ph})', v))

    async def delete(self, table: str = None, **where):
        if where:
            wc = " AND ".join(f'"{k}"=?' for k in where)
            params = list(where.values())
            await self._run(lambda: self.adapter.execute(f'DELETE FROM {table} WHERE {wc}', params))
        else:
            await self._run(lambda: self.adapter.execute(f'DELETE FROM {table}'))

    async def all(self, table: str = None, **where) -> list[dict]:
        return await self.get(table=table, **where) if where else await self.get(table=table)

    async def clear(self, table: str):
        await self.delete(table=table)

    # ── internal ──

    def _encode(self, col: str, val):
        if col in _JSON_COLS and not isinstance(val, str):
            return json.dumps(val, ensure_ascii=False) if val is not None else None
        return val

    def _decode_json(self, row: dict):
        for col in _JSON_COLS:
            if col in row and isinstance(row[col], str):
                try:
                    row[col] = json.loads(row[col])
                except (json.JSONDecodeError, TypeError):
                    pass
