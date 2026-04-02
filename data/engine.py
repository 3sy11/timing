"""行情接入：挂载 Parquet Client，HTTP router_mapping。"""
from timing.data.clients.file_parquet import FileParquetDataClient
from bollydog.models.service import AppService


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {"PushBars": ["POST", "/api/timing/push_bars"]}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.parquet_client = FileParquetDataClient()
        self.add_dependency(self.parquet_client)
