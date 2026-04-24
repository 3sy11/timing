"""DataEngine：TableCacheLayer(DuckDBProtocol) — 内存快读 + DuckDB 列式落盘，冷启动自动恢复。

额外维护 symbol_config 表，存放每个 symbol/interval 的覆盖参数(JSON)，
供下游服务 merge 到默认配置后做差异化分析。
"""
import json, logging
from typing import Dict, List
from bollydog.adapters.sqlalchemy import DuckDBProtocol
from bollydog.adapters.composite import TableCacheLayer
from bollydog.models.service import AppService
from timing.models.kline import KLINE_COLUMNS, KLINE_KEY_DEFS, kline_ddl
from timing.data.config import DataConfig

log = logging.getLogger(__name__)

_CFG_DDL = ('CREATE TABLE IF NOT EXISTS symbol_config '
            '("symbol" VARCHAR NOT NULL, "interval" VARCHAR NOT NULL, '
            '"config" VARCHAR, PRIMARY KEY ("symbol", "interval"))')


class DataEngine(AppService):
    domain = "timing"
    alias = "DataEngine"
    commands = ["models"]
    router_mapping = {
        "PushBars": ["POST", "/api/timing/push_bars"],
        "IngestKlinesFromFile": ["POST", "/api/timing/ingest_klines_from_file"],
        "SetSymbolConfig": ["POST", "/api/timing/set_symbol_config"],
    }

    def __init__(self, config: DataConfig = None, **kwargs):
        cfg = config or DataConfig()
        inner = DuckDBProtocol(url=cfg.db_path)
        proto = TableCacheLayer(protocol=inner, table='klines',
            key_columns=[k for k, _ in KLINE_KEY_DEFS],
            value_columns=KLINE_COLUMNS,
            sort_by='ts', ddl=kline_ddl(), flush_threshold=1)
        super().__init__(protocol=proto, **kwargs)
        self.add_dependency(proto)
        self._inner_db = inner
        self._symbol_configs: Dict[str, dict] = {}

    # ═══════ klines CRUD ═══════

    def get_klines(self, symbol: str, interval: str, start_ts: int = None, end_ts: int = None) -> List[dict]:
        rows = list(self.protocol.adapter.get(f"{symbol}:{interval}", []))
        if start_ts is not None: rows = [x for x in rows if x["ts"] >= start_ts]
        if end_ts is not None: rows = [x for x in rows if x["ts"] <= end_ts]
        return rows

    async def set_klines(self, symbol: str, interval: str, klines: List[dict]):
        await self.protocol.set(f"{symbol}:{interval}", klines)
        log.info(f'[Data] set_klines {symbol}/{interval} rows={len(klines)}')

    async def append_bars(self, symbol: str, interval: str, bars: List[dict]):
        key = f"{symbol}:{interval}"
        existing = list(self.protocol.adapter.get(key, []))
        processed = [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                       "close": float(b["close"]), "volume": float(b.get("volume", 0)), "ts": int(b["ts"])} for b in bars]
        existing.extend(processed)
        await self.protocol.set(key, existing)
        log.info(f'[Data] append_bars {symbol}/{interval} +{len(bars)} total={len(existing)}')

    # ═══════ symbol_config CRUD ═══════

    def get_symbol_config(self, symbol: str, interval: str) -> dict:
        return self._symbol_configs.get(f"{symbol}:{interval}", {})

    async def set_symbol_config(self, symbol: str, interval: str, config: dict):
        key = f"{symbol}:{interval}"
        self._symbol_configs[key] = config
        cfg_json = json.dumps(config, ensure_ascii=False)
        conn = self._inner_db.adapter
        conn.execute('DELETE FROM symbol_config WHERE symbol=? AND "interval"=?', [symbol, interval])
        conn.execute('INSERT INTO symbol_config (symbol, "interval", config) VALUES (?, ?, ?)',
                     [symbol, interval, cfg_json])
        log.info(f'[Data] set_symbol_config {symbol}/{interval} keys={list(config.keys())}')

    # ═══════ lifecycle ═══════

    async def on_start(self) -> None:
        await super().on_start()
        self._inner_db.adapter.execute(_CFG_DDL)
        rows = self._inner_db.adapter.execute('SELECT symbol, "interval", config FROM symbol_config').fetchall()
        for sym, intv, cfg_json in rows:
            self._symbol_configs[f"{sym}:{intv}"] = json.loads(cfg_json)
        if self._symbol_configs:
            log.info(f'[Data] cold-start loaded {len(self._symbol_configs)} symbol configs')
