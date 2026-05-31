"""StructuredSQLiteProtocol — 模型驱动的 SQLite 直连存储。

每张表由 TableSchema 描述，从 Pydantic 模型自动推导 DDL。
服务直接调用 get/put/append/delete，无缓存层。
"""
import json, os, logging
from dataclasses import dataclass, field
from pydantic import BaseModel
import aiosqlite

log = logging.getLogger(__name__)

_TYPE_MAP = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER", dict: "TEXT", list: "TEXT"}


@dataclass
class TableSchema:
    model: type
    table: str
    key_columns: list[str]
    singleton: bool = True
    sort_by: str = None
    json_columns: list[str] = field(default_factory=list)

    def __post_init__(self):
        fields = self.model.model_fields if hasattr(self.model, 'model_fields') else {}
        if not self.json_columns:
            self.json_columns = [n for n, f in fields.items()
                                 if getattr(f.annotation, '__origin__', f.annotation) in (dict, list)]

    @property
    def columns(self) -> list[str]:
        return list(self.model.model_fields.keys()) if hasattr(self.model, 'model_fields') else []

    def ddl(self) -> str:
        fields = self.model.model_fields
        parts = []
        for name, fi in fields.items():
            ann = fi.annotation
            if hasattr(ann, '__origin__'):
                ann = ann.__origin__
            sql_type = _TYPE_MAP.get(ann, "TEXT")
            parts.append(f'"{name}" {sql_type}')
        cols_def = ", ".join(parts)
        if self.singleton:
            pk = ", ".join(f'"{k}"' for k in self.key_columns)
            return f'CREATE TABLE IF NOT EXISTS "{self.table}" ({cols_def}, PRIMARY KEY ({pk}))'
        return f'CREATE TABLE IF NOT EXISTS "{self.table}" ({cols_def})'


class StructuredSQLiteProtocol:
    def __init__(self, path: str, schemas: list[TableSchema]):
        self.path = path
        self._schemas: dict[str, TableSchema] = {s.table: s for s in schemas}
        self._conn: aiosqlite.Connection = None

    async def on_start(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        for schema in self._schemas.values():
            await self._conn.execute(schema.ddl())
        await self._conn.commit()
        log.info(f'StructuredSQLiteProtocol on_start: {self.path} tables={list(self._schemas.keys())}')

    async def on_stop(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def get(self, table: str, **where):
        schema = self._schemas[table]
        sql, params = self._select_sql(schema, where)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        if schema.singleton:
            return self._decode_row(schema, rows[0]) if rows else None
        return [self._decode_row(schema, r) for r in rows]

    async def put(self, table: str, data, **where):
        schema = self._schemas[table]
        if schema.singleton:
            row = data if isinstance(data, dict) else data
            await self._upsert(schema, row)
        else:
            if where:
                wc, params = self._where_clause(where)
                await self._conn.execute(f'DELETE FROM "{schema.table}" WHERE {wc}', params)
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                await self._insert(schema, row)
        await self._conn.commit()

    async def append(self, table: str, data):
        schema = self._schemas[table]
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            await self._insert(schema, row)
        await self._conn.commit()

    async def delete(self, table: str, **where):
        schema = self._schemas[table]
        if where:
            wc, params = self._where_clause(where)
            await self._conn.execute(f'DELETE FROM "{schema.table}" WHERE {wc}', params)
        else:
            await self._conn.execute(f'DELETE FROM "{schema.table}"')
        await self._conn.commit()

    async def clear(self, table: str):
        await self._conn.execute(f'DELETE FROM "{self._schemas[table].table}"')
        await self._conn.commit()

    async def all(self, table: str) -> list[dict]:
        schema = self._schemas[table]
        sql = f'SELECT * FROM "{schema.table}"'
        if schema.sort_by:
            sql += f' ORDER BY "{schema.sort_by}"'
        async with self._conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [self._decode_row(schema, r) for r in rows]

    # ── internal ──

    def _select_sql(self, schema: TableSchema, where: dict) -> tuple[str, list]:
        sql = f'SELECT * FROM "{schema.table}"'
        params = []
        if where:
            wc, params = self._where_clause(where)
            sql += f' WHERE {wc}'
        if schema.sort_by:
            sql += f' ORDER BY "{schema.sort_by}"'
        return sql, params

    def _where_clause(self, where: dict) -> tuple[str, list]:
        parts = [f'"{k}"=?' for k in where]
        return " AND ".join(parts), list(where.values())

    def _decode_row(self, schema: TableSchema, row) -> dict:
        cols = schema.columns
        d = {cols[i]: row[i] for i in range(len(cols))}
        for jc in schema.json_columns:
            if jc in d and isinstance(d[jc], str):
                try:
                    d[jc] = json.loads(d[jc])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def _encode_value(self, schema: TableSchema, col: str, val):
        if col in schema.json_columns and not isinstance(val, str):
            return json.dumps(val, ensure_ascii=False)
        return val

    async def _upsert(self, schema: TableSchema, row: dict):
        cols = schema.columns
        values = [self._encode_value(schema, c, row.get(c)) for c in cols]
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(f'"{c}"' for c in cols)
        await self._conn.execute(
            f'INSERT OR REPLACE INTO "{schema.table}" ({col_names}) VALUES ({placeholders})', values)

    async def _insert(self, schema: TableSchema, row: dict):
        cols = schema.columns
        values = [self._encode_value(schema, c, row.get(c)) for c in cols]
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(f'"{c}"' for c in cols)
        await self._conn.execute(
            f'INSERT INTO "{schema.table}" ({col_names}) VALUES ({placeholders})', values)
