"""StorageService — 存储管理服务（占位）。未来负责小文件合并、归档、TTL。"""
import logging
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class StorageService(AppService):
    domain = "storage"
    alias = "StorageService"
    commands = ["timing.storage.command"]
