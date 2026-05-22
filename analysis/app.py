"""AnalysisEngine — 分析服务抽象基类。子类实现 _warmup + _process_bar。"""
import os, logging
from typing import ClassVar
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.globals import hub
from timing.data.models import GetKlines
from timing.models.signal import SignalEmitted

log = logging.getLogger(__name__)
CLOCK_MODULE = os.environ.get("TIMING_CLOCK", "timing.common.clock.LiveClock")
CACHE_PATH = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService, abstract=True):
    domain = "analysis"
    alias = "AnalysisEngine"
    clock = smart_import(CLOCK_MODULE)()
    _services: ClassVar[dict] = {}
    config = None

    def __init_subclass__(cls, abstract=False, **kwargs):
        if 'domain' not in cls.__dict__: cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        if not self.protocol:
            db_path = os.path.join(self._cache_path, f"{self.alias.lower()}.sqlite")
            inner = SQLiteProtocol(path=db_path)
            cache = CacheLayer(flush_threshold=1)
            cache.add_dependency(inner)
            self.protocol = cache
            await cache.maybe_start()
            log.info(f'[{self.alias}] 协议链就绪: CacheLayer → SQLite({db_path})')
        await super().on_start()

    async def on_started(self):
        AnalysisEngine._services[self.alias] = self
        log.info(f'[{self.alias}] 分析服务已注册')
        await super().on_started()

    async def on_bar(self, cmd):
        symbol = getattr(cmd, 'symbol', '') or ''
        interval = getattr(cmd, 'interval', '') or ''
        if not (symbol and interval): return None

        checkpoint_ts = await self.protocol.get(f"__ckpt:{symbol}:{interval}") or 0
        if checkpoint_ts == 0:
            all_klines = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            if not all_klines: return None
            warmup_size = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(all_klines) <= warmup_size: return None
            await self._warmup(symbol, interval, all_klines[:warmup_size])
            new_bars = all_klines[warmup_size:]
        else:
            new_bars = await hub.execute(GetKlines(symbol=symbol, interval=interval, start_ts=checkpoint_ts + 1))
        if not new_bars: return None

        output = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            bar_result = await self._process_bar(symbol, interval, bar)
            if bar_result:
                output["signals"].extend(bar_result.get("signals", []))
                output["breakouts"].extend(bar_result.get("breakouts", []))
                if bar_result.get("recomputed"): output["recomputed"] = True

        await self.protocol.set(f"__ckpt:{symbol}:{interval}", int(new_bars[-1]["ts"]))
        if output["signals"]:
            existing_signals = await self.protocol.get(f"signals:{symbol}:{interval}") or []
            existing_signals.extend(output["signals"])
            await self.protocol.set(f"signals:{symbol}:{interval}", existing_signals)

        for sig in output["signals"]:
            ev = SignalEmitted(
                ts=self.clock.now_ms(), symbol=symbol, interval=interval,
                direction=sig.get("direction", "neutral"), strength=sig.get("strength", 0.5),
                source=sig.get("source", self.alias), price=sig.get("touch_price", sig.get("price", 0.0)),
                level=sig.get("level_price", sig.get("level")),
                metadata={k: v for k, v in sig.items() if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level")})
            await hub.execute(ev)

        log.info(f'[{self.alias}] {symbol}/{interval} {len(new_bars)}根bar {len(output["signals"])}个信号')
        return output

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        raise NotImplementedError

    async def _warmup(self, symbol: str, interval: str, klines: list):
        raise NotImplementedError
