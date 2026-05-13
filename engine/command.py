"""RunBacktest — 回测命令，exchange.match + asyncio.gather 并行触发 on_bar。"""
import asyncio, logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.data.models import PushBars, GetKlines
from timing.analysis.app import AnalysisEngine

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        # 从 BacktestApp 配置或命令参数中获取 symbol/interval
        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")

        # 验证数据存在
        result = await hub.execute(GetKlines(symbol=symbol, interval=interval))
        klines = result.state.result()
        if not klines:
            log.warning(f'[Backtest] no klines for {symbol}/{interval}')
            return None

        # 构造 PushBars 作为 event carrier（不执行），供 on_bar 解析 symbol/interval
        push = PushBars(symbol=symbol, interval=interval, bars=[], replay=True)
        push.state.set_result({"symbol": symbol, "interval": interval, "bars": []})

        # 通过 Exchange 找到所有 PushBars subscriber handler，并行执行
        topic = type(push).destination
        handlers = list(hub.exchange.match(topic))
        cmds = []
        for handler_cls in handlers:
            cmd = handler_cls()
            cmd.add_event(push)
            cmds.append(cmd)

        log.info(f'[Backtest] start {symbol}/{interval} klines={len(klines)} handlers={len(cmds)}')
        results = await asyncio.gather(*(hub.execute(cmd) for cmd in cmds), return_exceptions=True)

        # 汇总结果
        errors = [r for r in results if isinstance(r, Exception)]
        if errors: log.error(f'[Backtest] {len(errors)} errors: {errors}')
        return {"symbol": symbol, "interval": interval,
                "services": len(AnalysisEngine._services), "klines_total": len(klines),
                "handlers": len(cmds), "errors": len(errors)}
