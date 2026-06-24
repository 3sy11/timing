"""AnalysisEngine — 分析服务抽象基类。子类实现 _warmup + _process_bar。"""
import os, logging
from typing import ClassVar
from mode.utils.imports import smart_import
from bollydog.models.service import AppService
from bollydog.globals import hub, services
from timing.adapters.duckdb import TimingDuckDBProtocol
from timing.models.events import SignalEmitted

log = logging.getLogger(__name__)
CLOCK_MODULE = os.environ.get("TIMING_CLOCK", "timing.common.clock.LiveClock")


class AnalysisEngine(AppService, abstract=True):
    domain = "analysis"
    alias = "AnalysisEngine"
    clock = smart_import(CLOCK_MODULE)()
    _services: ClassVar[dict] = {}
    config = None
    run_id: str = ""

    def __init_subclass__(cls, abstract=False, **kwargs):
        if 'domain' not in cls.__dict__:
            cls.domain = "analysis"
        super().__init_subclass__(abstract=abstract, **kwargs)

    def __init__(self, **kwargs):
        self.db: TimingDuckDBProtocol = None
        super().__init__(**kwargs)

    @property
    def data_engine(self):
        """通过 depends 或 services 获取 DataEngine 实例。"""
        if isinstance(self.depends, dict) and "data.DataEngine" in self.depends:
            return self.depends["data.DataEngine"]
        return services.get("data.DataEngine")

    async def on_start(self) -> None:
        self.db = TimingDuckDBProtocol.shared()
        if not self.db.adapter:
            await self.db.on_start()
        self.run_id = os.environ.get("TIMING_RUN_ID", "live_default")
        log.info(f'[{self.alias}] DB就绪, run_id={self.run_id}')
        await super().on_start()

    async def on_started(self):
        AnalysisEngine._services[self.alias] = self
        log.info(f'[{self.alias}] 分析服务已注册')
        await super().on_started()

    async def on_bar(self, cmd):
        symbol = getattr(cmd, 'symbol', '') or ''
        interval = getattr(cmd, 'interval', '') or ''
        if not (symbol and interval): return None

        ckpt = await self.db.get("analysis", run_id=self.run_id, symbol=symbol, interval=interval, name="checkpoint")
        checkpoint_ts = ckpt["ts"] if ckpt else 0
        de = self.data_engine
        if checkpoint_ts == 0:
            all_klines = de.get_klines(symbol, interval) if de else []
            if not all_klines: return None
            warmup_size = getattr(self.config, 'warmup_bars', 200) if self.config else 200
            if len(all_klines) <= warmup_size: return None
            await self._warmup(symbol, interval, all_klines[:warmup_size])
            new_bars = all_klines[warmup_size:]
        else:
            new_bars = de.get_klines(symbol, interval, start_ts=int(checkpoint_ts) + 1) if de else []
        if not new_bars: return None

        output = {"signals": [], "breakouts": [], "recomputed": False}
        for bar in new_bars:
            self.clock.set_time_ms(int(bar["ts"]))
            bar_result = await self._process_bar(symbol, interval, bar)
            if bar_result:
                output["signals"].extend(bar_result.get("signals", []))
                output["breakouts"].extend(bar_result.get("breakouts", []))
                if bar_result.get("recomputed"): output["recomputed"] = True

        await self.db.put("analysis", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                       "name": "checkpoint", "ts": int(new_bars[-1]["ts"]), "data": None})
        if output["signals"]:
            for sig in output["signals"]:
                await self.db.append("signals", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                                 "ts": sig.get("ts", self.clock.now_ms()),
                                                 "direction": sig.get("direction", "neutral"),
                                                 "strength": sig.get("strength", 0.0),
                                                 "price": sig.get("touch_price", sig.get("price", 0.0)),
                                                 "source": sig.get("source", self.alias),
                                                 "level": sig.get("level_price", sig.get("level", 0.0)),
                                                 "metadata": {k: v for k, v in sig.items()
                                                              if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level", "ts")}})
        for sig in output["signals"]:
            ev = SignalEmitted(
                ts=self.clock.now_ms(), symbol=symbol, interval=interval,
                direction=sig.get("direction", "neutral"), strength=sig.get("strength", 0.5),
                source=sig.get("source", self.alias),
                price=sig.get("touch_price", sig.get("price", 0.0)),
                level=sig.get("level_price", sig.get("level")),
                metadata={k: v for k, v in sig.items()
                          if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level")})
            await hub.execute(ev)
        log.info(f'[{self.alias}] {symbol}/{interval} {len(new_bars)}根bar {len(output["signals"])}个信号')
        return output

    async def on_stop(self):
        await super().on_stop()

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        raise NotImplementedError

    async def _warmup(self, symbol: str, interval: str, klines: list):
        raise NotImplementedError
