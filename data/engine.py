"""行情接入：HTTP router_mapping；读文件逻辑在 clients.file.read_file + models.IngestKlinesFromFile。"""
from bollydog.models.service import AppService


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestKlinesFromFile": ["POST", "/api/timing/ingest_klines_from_file"],
    }
