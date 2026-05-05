"""RunBacktest — 触发第一个 PushBars 事件，每个子服务的 on_bar 自动拉取并处理全部数据。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    """触发 PushBars → Exchange → on_bar，每个子服务自动拉取全部数据并处理。"""
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        from timing.data.models import GetKlines, PushBars
        from timing.analysis.engine import AnalysisEngine

        bt_app = app
        params = getattr(bt_app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")

        # 获取第一个 bar 作为 trigger
        get_cmd = GetKlines(symbol=symbol, interval=interval)
        klines_result = await hub.execute(get_cmd)
        klines = klines_result.state.result()
        if not klines:
            log.warning(f'[Backtest] no klines for {symbol}/{interval}')
            return None

        # 触发 PushBars → Exchange → on_bar（每个服务自动拉取全部数据）
        first_bar = klines[0]
        push = PushBars(symbol=symbol, interval=interval, bars=[first_bar], replay=True)
        await hub.execute(push)

        services = list(AnalysisEngine._services.values())
        log.info(f'[Backtest] triggered {symbol}/{interval} klines={len(klines)} services={len(services)}')
        return {"symbol": symbol, "interval": interval,
                "triggered": True, "klines_total": len(klines)}
