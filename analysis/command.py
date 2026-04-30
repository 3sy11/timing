"""AnalysisEngine fan-out command：OnBarReceived 遍历 _services 调 on_bar。"""
import logging
from typing import Any, ClassVar
from bollydog.models.base import BaseCommand

log = logging.getLogger(__name__)


class OnBarReceived(BaseCommand):
    """subscriber handler：PushBars 完成后 Exchange 触发 → fan-out 到所有注册的分析子服务。"""
    destination: ClassVar[str] = "analysis.AnalysisEngine.OnBarReceived"

    async def __call__(self, *args, **kwargs) -> Any:
        from timing.analysis.engine import AnalysisEngine
        event = self.get_event(-1)
        if not event: return None
        info = event.get("state", [None, None])[1]
        if not isinstance(info, dict): return None
        symbol, interval = info.get("symbol", ""), info.get("interval", "")
        bars = info.get("bars", [])
        if not (symbol and bars): return None
        for svc in AnalysisEngine._services.values():
            for bar in bars:
                await svc.on_bar(symbol, interval, bar)
        log.info(f'[AnalysisEngine] fan-out {symbol}/{interval} bars={len(bars)} services={len(AnalysisEngine._services)}')
