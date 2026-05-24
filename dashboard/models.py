"""Dashboard 数据模型。"""
import uuid, time
from typing import ClassVar
from pydantic import BaseModel, Field
from bollydog.models.base import BaseEvent


class BacktestRun(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "pending"  # pending/running/completed/failed
    symbol: str = ""
    interval: str = ""
    params: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    completed_at: int = 0
    error: str = ""


class BatchJob(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "pending"  # pending/running/completed
    symbol: str = ""
    interval: str = ""
    param_grid: dict = Field(default_factory=dict)
    warmup_bars: int = 200
    total_runs: int = 0
    completed_runs: int = 0
    runs: list[str] = Field(default_factory=list)
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))


class BacktestProgress(BaseEvent):
    destination: ClassVar[str] = "dashboard.DashboardService.BacktestProgress"
    job_id: str = ""
    run_index: int = 0
    total_runs: int = 0
    status: str = "running"
    params: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    run_id: str = ""
