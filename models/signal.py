"""Signal 数据模型 + SignalEmitted 事件。"""
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from bollydog.models.base import BaseEvent


class Signal(BaseModel):
    model_config = {"frozen": True}
    ts: int = 0
    symbol: str = ""
    interval: str = ""
    direction: Literal["long", "short", "neutral"] = "neutral"
    strength: float = 0.0
    source: str = ""
    price: float = 0.0
    level: float = None
    expires_at: int = None
    metadata: dict = Field(default_factory=dict)


class SignalEmitted(BaseEvent):
    destination: ClassVar[str] = "analysis.AnalysisEngine.SignalEmitted"
    ts: int = 0
    symbol: str = ""
    interval: str = ""
    direction: str = "neutral"
    strength: float = 0.0
    source: str = ""
    price: float = 0.0
    level: float = None
    expires_at: int = None
    metadata: dict = Field(default_factory=dict)
