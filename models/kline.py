"""Kline 数据模型。"""
from pydantic import BaseModel

_TYPE_MAP = {float: 'DOUBLE', int: 'BIGINT', str: 'VARCHAR'}
KEY_COLUMNS = ["symbol", "interval"]
SORT_BY = "ts"


class Kline(BaseModel):
    model_config = {"frozen": True}
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts: int


OHLCV = Bar = Kline
VALUE_COLUMNS = list(Kline.model_fields.keys())
ALL_COLUMNS = KEY_COLUMNS + VALUE_COLUMNS


def kline_ddl(table: str = 'klines') -> str:
    parts = [f'"{k}" {_TYPE_MAP[str]}' for k in KEY_COLUMNS]
    for name, fi in Kline.model_fields.items():
        parts.append(f'"{name}" {_TYPE_MAP.get(fi.annotation, "VARCHAR")}')
    return f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(parts)})'


def table_schema(table: str = 'klines', flush_threshold: int = 1) -> dict:
    return {"table": table, "key_columns": KEY_COLUMNS, "value_columns": VALUE_COLUMNS,
            "sort_by": SORT_BY, "ddl": kline_ddl(table), "flush_threshold": flush_threshold}
