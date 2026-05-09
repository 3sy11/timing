"""RunBacktest — exchange.match + asyncio.gather 并行触发所有 subscriber on_bar。"""
import asyncio, logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    """exchange.match 找到所有 PushBars subscriber handlers → gather 并行 execute。"""
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        from timing.data.models import PushBars, GetKlines
        from timing.analysis.engine import AnalysisEngine

        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")

        get_cmd = GetKlines(symbol=symbol, interval=interval)
        klines_result = await hub.execute(get_cmd)
        klines = klines_result.state.result()
        if not klines:
            log.warning(f'[Backtest] no klines for {symbol}/{interval}')
            return None

        # PushBars 仅作为 event carrier（不执行），手动 set_result 供 on_bar 解析
        push = PushBars(symbol=symbol, interval=interval, bars=[], replay=True)
        push.state.set_result({"symbol": symbol, "interval": interval, "bars": []})

        # exchange.match 找到所有订阅 PushBars 的 handler，gather 并行执行
        topic = type(push).destination
        handlers = list(hub.exchange.match(topic))
        cmds = []
        for h in handlers:
            cmd = h()
            cmd.add_event(push)
            cmds.append(cmd)

        log.info(f'[Backtest] {symbol}/{interval} klines={len(klines)} handlers={len(cmds)}')
        results = await asyncio.gather(*(hub.execute(cmd) for cmd in cmds), return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            log.error(f'[Backtest] {len(errors)} handler errors: {errors}')

        services = list(AnalysisEngine._services.values())
        return {"symbol": symbol, "interval": interval,
                "services": len(services), "klines_total": len(klines),
                "handlers": len(cmds), "errors": len(errors)}
