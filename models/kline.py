"""共享行情数据类型（K 线），Pydantic 模型 + 列元信息动态生成。"""
from pydantic import BaseModel

_TYPE_MAP = {float: 'DOUBLE', int: 'BIGINT', str: 'VARCHAR'}


class Kline(BaseModel):
    model_config = {"frozen": True}
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts: int

OHLCV = Kline
Bar = Kline

KLINE_COLUMNS = list(Kline.model_fields.keys())
KLINE_KEY_DEFS = [('symbol', 'VARCHAR'), ('interval', 'VARCHAR')]


def kline_ddl(table: str = 'klines') -> str:
    parts = [f'"{k}" {t}' for k, t in KLINE_KEY_DEFS]
    for name, fi in Kline.model_fields.items():
        db_type = _TYPE_MAP.get(fi.annotation, 'VARCHAR')
        parts.append(f'"{name}" {db_type}')
    return f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(parts)})'
