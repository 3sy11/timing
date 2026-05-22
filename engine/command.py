"""RunBacktest — 回测命令。"""
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
        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")

        klines = await hub.execute(GetKlines(symbol=symbol, interval=interval))
        if not klines:
            log.warning(f'[回测] {symbol}/{interval} 无数据，退出')
            return None

        # 清除生产 _services，只保留回测实例
        bt_aliases = {dep.alias for dep in (getattr(app, '_children', None) or []) if isinstance(dep, AnalysisEngine)}
        stale = [k for k in AnalysisEngine._services if k not in bt_aliases]
        for k in stale: del AnalysisEngine._services[k]
        log.info(f'[回测] 清除生产服务 {stale}，保留回测实例 {list(bt_aliases)}')

        # 清除 checkpoint
        for svc in AnalysisEngine._services.values():
            if svc.protocol and getattr(svc.protocol, 'protocol', None):
                await svc.protocol.remove(f"__ckpt:{symbol}:{interval}")
            elif svc.protocol:
                svc.protocol._cache.pop(f"__ckpt:{symbol}:{interval}", None)

        # 构造 PushBars 载体（replay=True 不写入数据库）
        push = PushBars(symbol=symbol, interval=interval, bars=[], replay=True)
        push.state.set_result({"symbol": symbol, "interval": interval, "bars": []})

        topic = type(push).destination
        handlers = list(hub.exchange.match(topic))
        cmds = []
        for handler_cls in handlers:
            cmd = handler_cls()
            cmd._source = push
            cmds.append(cmd)

        log.info(f'[回测] 开始 {symbol}/{interval} 共{len(klines)}根K线 {len(cmds)}个分析服务')
        results = await asyncio.gather(*(hub.execute(cmd) for cmd in cmds), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors: log.error(f'[回测] {len(errors)} 个错误: {errors}')

        # 收集中间结果
        signals, decisions, fills = [], [], []
        for svc in AnalysisEngine._services.values():
            if svc.protocol:
                sigs = await svc.protocol.get(f"signals:{symbol}:{interval}")
                if sigs: signals.extend(sigs)
        from timing.strategy.app import FibStrategy
        for svc in FibStrategy._apps.values():
            if hasattr(svc, 'protocol') and svc.protocol:
                decs = await svc.protocol.get(f"decisions:{symbol}")
                if decs: decisions.extend(decs)
        broker = None
        for svc in list(app._children or []):
            if getattr(svc, 'alias', '') == 'Broker':
                broker = svc; break
        if broker and broker.protocol:
            fill_keys = await broker.protocol.keys("__fills:*")
            for fk in fill_keys:
                f = await broker.protocol.get(fk)
                if f: fills.append(f)
        account = await broker.get_account() if broker else None
        positions = broker.get_all_positions() if broker else {}

        log.info(f'[回测] 完成 {symbol}/{interval} 信号={len(signals)} 决策={len(decisions)} 成交={len(fills)} 错误={len(errors)}')
        return {"symbol": symbol, "interval": interval, "klines_total": len(klines),
                "signals": signals, "decisions": decisions, "fills": fills,
                "account": account.model_dump() if account else {},
                "positions": {s: p.model_dump() for s, p in positions.items()},
                "errors": len(errors)}
