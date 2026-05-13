"""AnalysisEngine — 分析子服务 abstract 基类。

子服务继承本类，实现 _warmup / _process_bar。
生产：PushBars._fire → _publish → Exchange → on_bar（create_task 并发）
回测：RunBacktest → exchange.match + asyncio.gather(hub.execute) 并行

protocol 管理：
- TOML 配置 protocol 链时由 create_from 自动绑定 self.protocol
- 未配置时 on_start 创建默认 CacheLayer → SQLiteProtocol
- checkpoint / 缓存数据 均通过 self.protocol.get/set 持久化
"""
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
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, cache_path=None, **kwargs):
        self._cache_path = cache_path or CACHE_PATH
        os.makedirs(self._cache_path, exist_ok=True)
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        # TOML 未配置 protocol 时，创建默认持久化链
        if not self.protocol:
            db_path = os.path.join(self._cache_path, f"{self.alias.lower()}.sqlite")
            inner = SQLiteProtocol(path=db_path)
            self.protocol = CacheLayer(flush_threshold=1, protocol=inner)
            log.info(f'[{self.alias}] default protocol: {db_path}')
        await super().on_start()

    async def on_started(self):
        AnalysisEngine._services[self.alias] = self
        log.info(f'[AnalysisEngine] registered: {self.alias}')
        await super().on_started()

    async def on_bar(self, cmd):
        """Exchange subscriber handler — PushBars 事件触发入口。"""
        # 解析上游 PushBars 事件携带的 symbol/interval
        event = cmd.get_event(-1)
        if not event: return None
        state_result = event.get("state", [None, None])[1]
        if not isinstance(state_result, dict): return None
        symbol = state_result.get("symbol", "")
        interval = state_result.get("interval", "")
        if not (symbol and interval): return None

        # 读取 checkpoint 决定拉取范围
        checkpoint_ts = await self.protocol.get(f"__ckpt:{symbol}:{interval}") or 0

        if checkpoint_ts == 0:
            # 首次触发：全量拉取 → warmup 前 N 条 → 处理剩余
            result = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            all_klines = result.state.result()
            if not all_klines: return None
            warmup_size = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(all_klines) <= warmup_size: return None
            await self._warmup(symbol, interval, all_klines[:warmup_size])
            new_bars = all_klines[warmup_size:]
        else:
            # 增量触发：仅拉取 checkpoint 之后的数据
            result = await hub.execute(GetKlines(symbol=symbol, interval=interval, start_ts=checkpoint_ts + 1))
            new_bars = result.state.result()

        if not new_bars: return None

        # 按时间顺序逐条处理，汇总信号
        output = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            bar_result = await self._process_bar(symbol, interval, bar)
            if bar_result:
                output["signals"].extend(bar_result.get("signals", []))
                output["breakouts"].extend(bar_result.get("breakouts", []))
                if bar_result.get("recomputed"): output["recomputed"] = True

        # 更新 checkpoint
        await self.protocol.set(f"__ckpt:{symbol}:{interval}", int(new_bars[-1]["ts"]))

        # 广播信号事件给策略/风控订阅者
        for sig in output["signals"]:
            await hub.emit(SignalEmitted(
                ts=self.clock.now_ms(), symbol=symbol, interval=interval,
                direction=sig.get("direction", "neutral"),
                strength=sig.get("strength", 0.5),
                source=sig.get("source", self.alias),
                price=sig.get("touch_price", sig.get("price", 0.0)),
                level=sig.get("level_price", sig.get("level")),
                metadata={k: v for k, v in sig.items() if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level")}
            ))

        log.info(f'[{self.alias}] on_bar {symbol}/{interval} bars={len(new_bars)} signals={len(output["signals"])}')
        return output

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        raise NotImplementedError

    async def _warmup(self, symbol: str, interval: str, klines: list):
        raise NotImplementedError
