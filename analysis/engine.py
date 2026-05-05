"""AnalysisEngine — 子服务基类，通过 Exchange subscriber 接收 PushBars 事件。

子服务（RetracementService 等）继承本类，实现 _warmup / _process_bar。
生产：PushBars → Exchange → on_bar（每个实例并发）
回测：RunBacktest 触发第一个 PushBars → on_bar 自动拉取全部数据
"""
import os, logging
from typing import ClassVar, Dict
from mode.utils.imports import smart_import
from bollydog.models.service import AppService

log = logging.getLogger(__name__)

CLOCK_MODULE = os.environ.get("TIMING_CLOCK", "timing.common.clock.LiveClock")
CACHE_PATH = os.environ.get("TIMING_ANALYSIS_CACHE_PATH", "cache/analysis")


class AnalysisEngine(AppService):
    domain = "analysis"
    alias = "AnalysisEngine"
    commands = ["timing.analysis.command"]
    clock = smart_import(CLOCK_MODULE)()
    _services: ClassVar[dict] = {}
    _checkpoints: ClassVar[Dict[tuple, int]] = {}  # (symbol, interval) -> last processed ts
    config = None

    def __init_subclass__(cls, abstract=False, **kwargs):
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    async def on_started(self):
        if type(self) is not AnalysisEngine:
            AnalysisEngine._services[self.alias] = self
            log.info(f'[AnalysisEngine] _services registered: {self.alias}')
        await super().on_started()

    async def on_bar(self, cmd):
        """Exchange subscriber: PushBars 事件触发。
        检查 checkpoint → 拉取新数据 → warmup(首次) → 按序处理 bar → 更新 checkpoint。
        """
        event = cmd.get_event(-1)
        if not event:
            return None
        info = event.get("state", [None, None])[1]
        if not isinstance(info, dict):
            return None
        symbol, interval = info.get("symbol", ""), info.get("interval", "")
        if not (symbol and interval):
            return None

        checkpoint_ts = self._checkpoints.get((symbol, interval), 0)

        # 拉取全部数据
        from bollydog.globals import hub
        from timing.data.models import GetKlines
        get_cmd = GetKlines(symbol=symbol, interval=interval)
        klines_result = await hub.execute(get_cmd)
        klines = klines_result.state.result()
        if not klines:
            return None

        # 过滤 checkpoint 之后的新 bar
        new_bars = [b for b in klines if int(b["ts"]) > checkpoint_ts]
        if not new_bars:
            return None

        # 首次：warmup
        if checkpoint_ts == 0:
            warmup = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(klines) <= warmup:
                return None
            await self._warmup(symbol, interval, klines[:warmup])
            new_bars = [b for b in new_bars if int(b["ts"]) > int(klines[warmup - 1]["ts"])]

        # 按序处理
        result = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            r = await self._process_bar(symbol, interval, bar)
            if r:
                result["signals"].extend(r.get("signals", []))
                result["breakouts"].extend(r.get("breakouts", []))
                if r.get("recomputed"):
                    result["recomputed"] = True

        # 更新 checkpoint
        self._checkpoints[(symbol, interval)] = int(new_bars[-1]["ts"])

        log.info(f'[{self.alias}] on_bar {symbol}/{interval} '
                 f'new={len(new_bars)} signals={len(result["signals"])}')
        return result

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        """处理单个 bar。子类实现具体逻辑。"""
        raise NotImplementedError

    async def _warmup(self, symbol, interval, klines):
        raise NotImplementedError
