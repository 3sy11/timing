"""RunBacktest — 遍历 _services warmup → 逐 bar 直接调 on_bar（保证顺序）。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200

    async def __call__(self, *args, **kwargs) -> Any:
        bt_app = app
        data_engine = AppService._apps.get('data.DataEngine')
        if not data_engine:
            log.warning('[Backtest] DataEngine not found'); return None
        params = getattr(bt_app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")
        warmup = self.warmup_bars or params.get("warmup_bars", 200)
        klines = data_engine.get_klines(symbol, interval)
        n = len(klines)
        if n <= warmup:
            log.warning(f'[Backtest] klines({n}) <= warmup({warmup})'); return None
        clock = bt_app.analysis.clock
        services = list(bt_app.analysis._services.values())
        log.info(f'[Backtest] start {symbol}/{interval} klines={n} warmup={warmup} services={len(services)}')
        for svc in services:
            await svc._warmup(symbol, interval, klines[:warmup])
        for bar in klines[warmup:]:
            clock.set_time_ms(int(bar["ts"]))
            for svc in services:
                await svc.on_bar(symbol, interval, bar)
        log.info(f'[Backtest] done {symbol}/{interval} bars_replayed={n - warmup}')
        return {"symbol": symbol, "interval": interval, "bars_replayed": n - warmup}
