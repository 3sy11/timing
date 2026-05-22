"""StrategyDecision — 策略决策记录。"""
from pydantic import BaseModel


class StrategyDecision(BaseModel):
    model_config = {"frozen": True}
    ts: int = 0
    symbol: str = ""
    direction: str = "neutral"
    strength: float = 0.0
    price: float = 0.0
    action: str = ""
    reason: str = ""
