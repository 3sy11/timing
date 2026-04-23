"""DataEngine 配置，db_path 从环境变量读取。"""
import os
from dataclasses import dataclass


@dataclass
class DataConfig:
    db_path: str = os.environ.get("TIMING_DATA_DB_PATH", "cache/data.duckdb")
