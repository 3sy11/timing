"""RunBacktest — 逐bar回测命令。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.data.models import GetKlines
from timing.analysis.app import AnalysisEngine
from timing.common.clock import SimulatedClock
from timing.models.signal import SignalEmitted

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200

    async def __call__(self, *args, **kwargs) -> Any:
        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")
        warmup_n = self.warmup_bars or params.get("warmup_bars", 200)

        klines = await hub.execute(GetKlines(symbol=symbol, interval=interval))
        if not klines:
            log.warning(f'[回测] {symbol}/{interval} 无数据')
            return None
        if len(klines) <= warmup_n:
            log.warning(f'[回测] K线数{len(klines)} ≤ warmup{warmup_n}，不足')
            return None

        # 定位服务
        analysis_svcs = list(AnalysisEngine._services.values())
        broker = next((s for s in (app._children or []) if getattr(s, 'alias', '') == 'Broker'), None)
        if not analysis_svcs:
            log.error('[回测] 无分析服务'); return None
        if not broker:
            log.error('[回测] 无Broker'); return None

        # 注入模拟时钟
        clock = SimulatedClock()
        AnalysisEngine.clock = clock

        # 重置分析服务 checkpoint + warmup
        warmup_data = klines[:warmup_n]
        replay_data = klines[warmup_n:]
        for svc in analysis_svcs:
            if svc.protocol:
                await svc.protocol.remove(f"__ckpt:{symbol}:{interval}")
                await svc.protocol.remove(f"signals:{symbol}:{interval}")
                await svc.protocol.remove(f"_touch:{symbol}:{interval}")
            await svc._warmup(symbol, interval, warmup_data)
        log.info(f'[回测] {symbol}/{interval} warmup={warmup_n} replay={len(replay_data)} 分析服务={len(analysis_svcs)}')

        # 逐bar循环
        all_signals = []
        for i, bar in enumerate(replay_data):
            clock.set_time_ms(int(bar["ts"]))

            # 1. 先处理挂单（限价/止损）
            await broker.process_pending(bar)

            # 2. 每个分析服务处理当前bar
            for svc in analysis_svcs:
                result = await svc._process_bar(symbol, interval, bar)
                if not result: continue
                signals = result.get("signals", [])
                if not signals: continue

                # 持久化信号
                existing = await svc.protocol.get(f"signals:{symbol}:{interval}") or []
                existing.extend(signals)
                await svc.protocol.set(f"signals:{symbol}:{interval}", existing)

                # 3. 每个信号立即广播 → 策略 → 下单 → 成交（同步链路）
                for sig in signals:
                    all_signals.append(sig)
                    ev = SignalEmitted(
                        ts=clock.now_ms(), symbol=symbol, interval=interval,
                        direction=sig.get("direction", "neutral"),
                        strength=sig.get("strength", 0.5),
                        source=sig.get("source", svc.alias),
                        price=sig.get("touch_price", sig.get("price", 0.0)),
                        level=sig.get("level_price", sig.get("level")),
                        metadata={k: v for k, v in sig.items() if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level")})
                    await hub.execute(ev)

            if (i + 1) % 50 == 0:
                log.info(f'[回测] 进度 {i+1}/{len(replay_data)} 信号={len(all_signals)}')

        # 收集结果
        signals_out, decisions, fills = [], [], []
        for svc in analysis_svcs:
            if svc.protocol:
                sigs = await svc.protocol.get(f"signals:{symbol}:{interval}")
                if sigs: signals_out.extend(sigs)

        from timing.strategy.app import FibStrategy
        for svc in FibStrategy._apps.values():
            if hasattr(svc, 'protocol') and svc.protocol:
                decs = await svc.protocol.get(f"decisions:{symbol}")
                if decs: decisions.extend(decs)

        if broker.protocol:
            fill_keys = await broker.protocol.keys("__fills:*")
            for fk in (fill_keys or []):
                f = await broker.protocol.get(fk)
                if f: fills.append(f)

        account = await broker.get_account()
        positions = broker.get_all_positions()

        log.info(f'[回测] 完成 {symbol}/{interval} 信号={len(signals_out)} 决策={len(decisions)} 成交={len(fills)}')
        return {"symbol": symbol, "interval": interval, "klines": klines,
                "signals": signals_out, "decisions": decisions, "fills": fills,
                "account": account.model_dump() if account else {},
                "positions": {s: p.model_dump() for s, p in positions.items()}}
