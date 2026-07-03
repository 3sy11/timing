"""StorageService Commands — Compact（占位）。"""
import logging
from typing import Any, ClassVar
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class Compact(BaseCommand):
    """合并小 parquet 文件（占位，待实现）。"""
    destination: ClassVar[str] = "storage.StorageService.Compact"
    table: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        log.info(f'[存储] Compact {self.table} — 暂未实现')
        return {"status": "not_implemented"}
