"""行情接入：挂载各 Client，HTTP router_mapping。"""
from timing.data.clients.file_parquet import FileParquetDataClient
from timing.data.clients.list_client import ListDataClient
from bollydog.models.service import AppService


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {"PushBars": ["POST", "/api/timing/push_bars"]}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.list_client = ListDataClient()
        self.parquet_client = FileParquetDataClient()
        self.add_dependency(self.list_client)
        self.add_dependency(self.parquet_client)
