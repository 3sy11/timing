"""AnalysisEngine — 分析子服务 abstract 基类。

子服务（RetracementService 等）继承本类，实现 _warmup / _process_bar。
通过 Exchange subscriber 接收 PushBars 事件触发 on_bar 工作流。
生产：PushBars._fire → _publish → Exchange → on_bar（每个实例并发 create_task）
回测：RunBacktest → exchange.match + asyncio.gather(hub.execute) 并行
"""
import os, logging
from typing import ClassVar, Dict
from mode.utils.imports import smart_import
from bollydog.models.service import AppService

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
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        self._checkpoints: Dict[tuple, int] = {}
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    async def on_started(self):
        AnalysisEngine._services[self.alias] = self
        log.info(f'[AnalysisEngine] registered: {self.alias}')
        await super().on_started()

    async def on_bar(self, cmd):
        """Exchange subscriber handler: PushBars event → checkpoint → pull data → warmup → process."""
        event = cmd.get_event(-1)
        if not event: return None
        info = event.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        symbol, interval = info.get("symbol", ""), info.get("interval", "")
        if not (symbol and interval): return None

        from bollydog.globals import hub
        from timing.data.models import GetKlines

        checkpoint_ts = self._checkpoints.get((symbol, interval), 0)
        if checkpoint_ts == 0:
            get_cmd = GetKlines(symbol=symbol, interval=interval)
            klines_result = await hub.execute(get_cmd)
            all_klines = klines_result.state.result()
            if not all_klines: return None
            warmup = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(all_klines) <= warmup: return None
            await self._warmup(symbol, interval, all_klines[:warmup])
            new_bars = all_klines[warmup:]
        else:
            get_cmd = GetKlines(symbol=symbol, interval=interval, start_ts=checkpoint_ts + 1)
            klines_result = await hub.execute(get_cmd)
            new_bars = klines_result.state.result()

        if not new_bars: return None
        result = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            r = await self._process_bar(symbol, interval, bar)
            if r:
                result["signals"].extend(r.get("signals", []))
                result["breakouts"].extend(r.get("breakouts", []))
                if r.get("recomputed"): result["recomputed"] = True
        self._checkpoints[(symbol, interval)] = int(new_bars[-1]["ts"])
        log.info(f'[{self.alias}] on_bar {symbol}/{interval} new={len(new_bars)} signals={len(result["signals"])}')
        return result

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        raise NotImplementedError

    async def _warmup(self, symbol, interval, klines):
        raise NotImplementedError
