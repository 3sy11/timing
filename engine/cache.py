"""CacheEngine：系统内唯一数据真相源。内部存 List[dict]。
遵循 SKILL.md：Command 通过 globals.app 访问所属 Service。"""
import logging
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class OnBarReceived(BaseCommand):
    """subscriber: timing.DataEngine.PushBars → bars 写入 Cache。"""
    destination: ClassVar[str] = "timing.CacheEngine.OnBarReceived"
    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        sym, interval, bars = info.get("symbol", ""), info.get("interval", ""), info.get("bars", [])
        if not (sym and bars): return None
        for bar in bars: app.append_bar(sym, interval, bar)
        log.info(f'[Cache] append_bar {sym}/{interval} +{len(bars)} bars, total={len(app.get_klines(sym, interval))}')
        return {"symbol": sym, "interval": interval, "count": len(bars)}


class OnDataIngested(BaseCommand):
    """subscriber: timing.DataEngine.IngestKlinesFromFile → 批量替换。上游 klines: list[dict]。"""
    destination: ClassVar[str] = "timing.CacheEngine.OnDataIngested"
    async def __call__(self, *args, **kwargs) -> Any:
        raw = self.get_event(-1)
        if not raw: return None
        info = raw.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        sym, interval = info.get("symbol", ""), info.get("interval", "")
        klines = info.get("klines", [])
        if not (sym and klines): return None
        rev = app.replace_klines(sym, interval, klines)
        log.info(f'[Cache] replace_klines {sym}/{interval} rows={len(klines)} rev={rev}')
        return {"revision": rev, "rows": len(klines), "symbol": sym, "interval": interval}


class GetKlines(BaseCommand):
    destination: ClassVar[str] = "timing.CacheEngine.GetKlines"
    qos: int = 0
    symbol: str = ""
    interval: str = ""
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    async def __call__(self, *args, **kwargs) -> Any:
        return app.get_klines(self.symbol, self.interval, self.start_ts, self.end_ts)


class CacheEngine(AppService):
    domain = "timing"
    alias = "CacheEngine"
    commands = ["commands"]
    subscriber = {
        "timing.DataEngine.PushBars": OnBarReceived,
        "timing.DataEngine.IngestKlinesFromFile": OnDataIngested,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store: Dict[Tuple[str, str], List[dict]] = {}
        self._revision: Dict[Tuple[str, str], int] = {}

    def append_bar(self, symbol: str, interval: str, bar: dict) -> None:
        key = (symbol, interval)
        d = {"open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]),
             "close": float(bar["close"]), "volume": float(bar.get("volume", 0)), "ts": int(bar["ts"])}
        self._store.setdefault(key, []).append(d)
        self._store[key].sort(key=lambda x: x["ts"])

    def replace_klines(self, symbol: str, interval: str, klines: List[dict]) -> int:
        key = (symbol, interval)
        self._store[key] = sorted(klines, key=lambda x: x["ts"])
        self._revision[key] = self._revision.get(key, 0) + 1
        return self._revision[key]

    def revision(self, symbol: str, interval: str) -> int:
        return self._revision.get((symbol, interval), 0)

    def get_klines(self, symbol: str, interval: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[dict]:
        rows = list(self._store.get((symbol, interval), []))
        if start_ts is not None: rows = [x for x in rows if x["ts"] >= start_ts]
        if end_ts is not None: rows = [x for x in rows if x["ts"] <= end_ts]
        return rows

    async def on_reset(self) -> None:
        self._store.clear()
        self._revision.clear()
