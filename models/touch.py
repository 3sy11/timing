"""TouchSignal + TouchEntry — 触碰信号与去重记录。"""
from pydantic import BaseModel


class TouchSignal(BaseModel):
    symbol: str = ""
    interval: str = ""
    ts: int = 0
    direction: str = "neutral"
    strength: float = 0.0
    ratio: float = 0.0
    level_price: float = 0.0
    touch_price: float = 0.0
    group_idx: int = 0


class TouchEntry(BaseModel):
    symbol: str = ""
    interval: str = ""
    level_key: str = ""
    last_ts: int = 0
