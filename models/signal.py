"""Signal — 交易信号数据模型 + SignalEmitted 事件。

Signal：纯数据模型，可持久化/序列化/回测收集。
SignalEmitted：BaseEvent，字段与 Signal 一致，走 Exchange pub/sub。
分析服务 on_bar 产出信号后 emit(SignalEmitted(...)) 广播给策略订阅者。
"""
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from bollydog.models.base import BaseEvent


class Signal(BaseModel):
    """交易信号数据模型 — 不依赖框架，纯序列化存储。"""
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
    """信号事件 — 走 Exchange pub/sub 广播给策略/风控订阅者。"""
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
