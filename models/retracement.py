"""Retracement — 回撤结构缓存（data 为 JSON）。"""
from pydantic import BaseModel, Field


class Retracement(BaseModel):
    symbol: str = ""
    interval: str = ""
    data: dict = Field(default_factory=dict)
