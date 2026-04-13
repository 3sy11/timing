"""行情接入：挂载文件数据源，HTTP router_mapping。"""
from timing.data.clients.file import FileDataClient
from bollydog.models.service import AppService


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestParquetFile": ["POST", "/api/timing/ingest_parquet_file"],
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.file_client = FileDataClient()
        self.add_dependency(self.file_client)
