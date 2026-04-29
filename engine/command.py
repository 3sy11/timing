"""RunBacktest — 实例化新子服务实现隔离 + 逐 bar dispatch PushBars 回放。"""
import logging
from typing import Any, ClassVar, Dict, List, Optional
from pydantic import Field
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.data.models import PushBars
from timing.analysis.algo.retracement.config import RetracementConfig
from timing.analysis.algo.retracement.algo import compute_retracement

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    """apply_config → compute_retracement 初始化 → 逐 bar dispatch PushBars(replay)。"""
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    services: Optional[List[str]] = None
    config: Dict[str, Any] = Field(default_factory=dict)

    async def __call__(self, *args, **kwargs) -> Any:
        bt_app = app
        klines = bt_app.data.get_klines(self.symbol, self.interval)
        n = len(klines)
        if n <= self.warmup_bars:
            log.warning(f'[Backtest] klines({n}) <= warmup_bars({self.warmup_bars})'); return None
        analysis = bt_app.analysis
        svc = analysis.retracement
        cfg = svc.config
        if self.config: cfg.apply_overrides(self.config)
        overrides = await analysis.get_symbol_overrides(self.symbol, self.interval)
        if overrides: cfg.apply_overrides(overrides)
        result = compute_retracement(klines[:self.warmup_bars], cfg)
        await svc.set_cache(self.symbol, self.interval, result)
        log.info(f'[Backtest] start {self.symbol}/{self.interval} klines={n} warmup={self.warmup_bars} groups={len(result.get("groups", []))}')
        all_results = []
        topic = PushBars.destination
        for i in range(self.warmup_bars, n):
            bt_app.clock.set_time_ms(int(klines[i]["ts"]))
            push = PushBars(symbol=self.symbol, interval=self.interval, bars=[klines[i]], replay=True)
            await hub.execute(push)
            for handler_cls in hub.exchange.match(topic):
                cmd = handler_cls()
                cmd.add_event(push)
                await hub.execute(cmd)
                r = cmd.state.result() if cmd.state.done() else {}
                if r and (r.get("touched") or r.get("broken")):
                    all_results.append({"bar_idx": i, "ts": int(klines[i]["ts"]), **r})
        log.info(f'[Backtest] done {self.symbol}/{self.interval} bars={n - self.warmup_bars} signals={len(all_results)}')
        return {"symbol": self.symbol, "interval": self.interval, "results": all_results,
                "klines_total": n, "warmup_bars": self.warmup_bars, "test_bars": n - self.warmup_bars}
