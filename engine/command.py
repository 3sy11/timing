"""
RunBacktest — 回测命令。

【执行流程】
  1. 从 DataEngine 拉取指定 symbol/interval 的全部 K 线
  2. 清除所有分析服务的 checkpoint（确保每次回测都从头跑）
  3. 构造一个假的 PushBars 事件作为载体
  4. 通过 Exchange 匹配所有订阅了 PushBars 的 handler（即各分析服务的 on_bar）
  5. asyncio.gather 并行执行所有 handler
  6. 在 handler 内部，信号会同步传递给策略层和执行层

【使用方式】
  .venv/bin/python3 main.py execute RunBacktest --config config.toml --symbol 159363.OF --interval 1d
"""
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
        # ① 确定回测标的和周期
        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")

        # ② 从 DataEngine 获取全部 K 线数据
        result = await hub.execute(GetKlines(symbol=symbol, interval=interval))
        klines = result.state.result()
        if not klines:
            log.warning(f'[回测] {symbol}/{interval} 无数据，退出')
            return None

        # ③ 清除 checkpoint — 确保分析服务从头处理所有数据
        for svc in AnalysisEngine._services.values():
            if svc.protocol and getattr(svc.protocol, 'protocol', None):
                await svc.protocol.remove(f"__ckpt:{symbol}:{interval}")
            elif svc.protocol:
                svc.protocol._cache.pop(f"__ckpt:{symbol}:{interval}", None)

        # ④ 构造 PushBars 事件载体（replay=True 表示不写入数据库）
        push = PushBars(symbol=symbol, interval=interval, bars=[], replay=True)
        push.state.set_result({"symbol": symbol, "interval": interval, "bars": []})

        # ⑤ 从 Exchange 找到所有订阅了 PushBars 的 handler（各分析服务的 on_bar）
        topic = type(push).destination
        handlers = list(hub.exchange.match(topic))
        cmds = []
        for handler_cls in handlers:
            cmd = handler_cls()
            cmd.add_event(push)
            cmds.append(cmd)

        log.info(f'[回测] 开始 {symbol}/{interval} 共{len(klines)}根K线 {len(cmds)}个分析服务')

        # ⑥ 并行执行所有分析服务的 on_bar（内部信号会同步传给策略→执行层）
        results = await asyncio.gather(*(hub.execute(cmd) for cmd in cmds), return_exceptions=True)

        # ⑦ 统计结果
        errors = [r for r in results if isinstance(r, Exception)]
        if errors: log.error(f'[回测] {len(errors)} 个错误: {errors}')
        log.info(f'[回测] 完成 {symbol}/{interval} 错误={len(errors)}')
        return {"symbol": symbol, "interval": interval,
                "services": len(AnalysisEngine._services), "klines_total": len(klines),
                "handlers": len(cmds), "errors": len(errors)}
