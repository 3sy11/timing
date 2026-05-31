"""Checkpoint — 分析服务处理进度标记。"""
from pydantic import BaseModel


class Checkpoint(BaseModel):
    symbol: str = ""
    interval: str = ""
    ts: int = 0
