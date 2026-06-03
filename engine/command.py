"""RunBacktest — 多品种多参数回测命令。

每个 AnalysisEngine service 实例携带独立的 (symbol, interval, warmup_bars)，
RunBacktest 遍历所有实例，按各自参数独立 warmup + replay。
同一个 run_id 下产出所有品种的 signals/orders/positions。
"""
import time, logging, uuid
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.data.models import GetKlines
from timing.adapters.duckdb import TimingDuckDBProtocol
from timing.analysis.app import AnalysisEngine
from timing.common.clock import SimulatedClock
from timing.models.events import SignalEmitted

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    run_id: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        params = getattr(app, '_bt_params', {})
        run_id = self.run_id or params.get("run_id", "") or f"bt_{uuid.uuid4().hex[:6]}"

        analysis_svcs = list(AnalysisEngine._services.values())
        broker = next((s for s in (app._children or []) if getattr(s, 'alias', '') == 'Broker'), None)
        if not analysis_svcs:
            log.error('[回测] 无分析服务'); return None
        if not broker:
            log.error('[回测] 无Broker'); return None

        db = TimingDuckDBProtocol.shared()
        clock = SimulatedClock()
        AnalysisEngine.clock = clock

        # 设置 run_id
        for svc in analysis_svcs:
            svc.run_id = run_id
        broker.run_id = run_id
        from timing.strategy.app import FibStrategy
        for svc in FibStrategy._apps.values() if hasattr(FibStrategy, '_apps') else []:
            svc.run_id = run_id

        # 收集所有 (symbol, interval) 组合
        tasks = []
        for svc in analysis_svcs:
            symbol = getattr(svc, '_bt_symbol', '') or params.get("symbol", "")
            interval = getattr(svc, '_bt_interval', '') or params.get("interval", "1d")
            warmup_n = getattr(svc, '_bt_warmup_bars', 0) or params.get("warmup_bars", 200)
            if not symbol:
                log.warning(f'[回测] {svc.alias} 未配置 symbol，跳过'); continue
            tasks.append({"svc": svc, "symbol": symbol, "interval": interval, "warmup_bars": warmup_n})

        symbols_desc = ", ".join(f'{t["symbol"]}/{t["interval"]}' for t in tasks)
        await db.put("runs", {"run_id": run_id, "created_at": int(time.time()),
                              "status": "running", "mode": "backtest",
                              "description": symbols_desc,
                              "params": {"services": len(tasks), "symbols": symbols_desc}})
        log.info(f'[回测] run_id={run_id} 共{len(tasks)}个任务: {symbols_desc}')

        total_signals = 0
        for task in tasks:
            svc, symbol, interval, warmup_n = task["svc"], task["symbol"], task["interval"], task["warmup_bars"]
            klines = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            if not klines:
                log.warning(f'[回测] {svc.alias} {symbol}/{interval} 无数据，跳过'); continue
            if len(klines) <= warmup_n:
                log.warning(f'[回测] {svc.alias} {symbol}/{interval} K线{len(klines)} ≤ warmup{warmup_n}，跳过'); continue

            warmup_data = klines[:warmup_n]
            replay_data = klines[warmup_n:]
            await svc._warmup(symbol, interval, warmup_data)
            log.info(f'[回测] {svc.alias} {symbol}/{interval} warmup={warmup_n} replay={len(replay_data)}')

            svc_signals = 0
            for i, bar in enumerate(replay_data):
                clock.set_time_ms(int(bar["ts"]))
                await broker.process_pending(bar)
                result = await svc._process_bar(symbol, interval, bar)
                if not result:
                    continue
                signals = result.get("signals", [])
                for sig in signals:
                    svc_signals += 1
                    await db.append("signals", {"run_id": run_id, "symbol": symbol, "interval": interval,
                                                "ts": sig.get("ts", clock.now_ms()),
                                                "direction": sig.get("direction", "neutral"),
                                                "strength": sig.get("strength", 0.0),
                                                "price": sig.get("touch_price", sig.get("price", 0.0)),
                                                "source": sig.get("source", svc.alias),
                                                "level": sig.get("level_price", sig.get("level", 0.0)),
                                                "metadata": {k: v for k, v in sig.items()
                                                             if k not in ("direction", "strength", "source", "touch_price", "price", "level_price", "level", "ts")}})
                    ev = SignalEmitted(ts=clock.now_ms(), symbol=symbol, interval=interval,
                                      direction=sig.get("direction", "neutral"), strength=sig.get("strength", 0.5),
                                      source=sig.get("source", svc.alias),
                                      price=sig.get("touch_price", sig.get("price", 0.0)),
                                      level=sig.get("level_price", sig.get("level")))
                    await hub.execute(ev)
                if (i + 1) % 50 == 0:
                    log.info(f'[回测] {svc.alias} 进度 {i+1}/{len(replay_data)} 信号={svc_signals}')

            total_signals += svc_signals
            log.info(f'[回测] {svc.alias} {symbol}/{interval} 完成 信号={svc_signals}')

        await db.put("runs", {"run_id": run_id, "created_at": int(time.time()),
                              "status": "completed", "mode": "backtest",
                              "description": symbols_desc,
                              "params": {"services": len(tasks), "symbols": symbols_desc}})

        fills = await db.get("fills", run_id=run_id)
        fills = fills if isinstance(fills, list) else ([fills] if fills else [])
        positions = await db.get("positions", run_id=run_id)
        positions = positions if isinstance(positions, list) else ([positions] if positions else [])
        account = await broker.get_account()

        log.info(f'[回测] 全部完成 run_id={run_id} 信号={total_signals} 成交={len(fills)}')
        return {"run_id": run_id, "services": len(tasks), "symbols": symbols_desc,
                "signals_count": total_signals, "fills_count": len(fills),
                "positions": positions, "account": account}
