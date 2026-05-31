"""BatchBacktest — 批量参数扫描回测命令。"""
import itertools, logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.analysis.app import AnalysisEngine
from timing.strategy.app import FibStrategy
from timing.engine.command import RunBacktest
from timing.dashboard.models import BacktestProgress
from timing.common.metrics import compute_metrics

log = logging.getLogger(__name__)


class BatchBacktest(BaseCommand):
    destination: ClassVar[str] = "backtest.BacktestApp.BatchBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    param_grid: dict = {}

    async def __call__(self, *args, **kwargs) -> Any:
        params = getattr(app, '_bt_params', {})
        symbol = self.symbol or params.get("symbol", "")
        interval = self.interval or params.get("interval", "")
        grid = self.param_grid or params.get("param_grid", {})
        if not grid:
            log.warning('[批量回测] param_grid 为空，执行单次回测')
            result = await hub.execute(RunBacktest(symbol=symbol, interval=interval, warmup_bars=self.warmup_bars))
            return [{"params": {}, "result": result}]

        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        log.info(f'[批量回测] {symbol}/{interval} 共{len(combos)}组参数组合')

        strategy_keys = {"position_size", "min_strength"}
        results = []

        for idx, combo in enumerate(combos):
            log.info(f'[批量回测] [{idx+1}/{len(combos)}] 参数={combo}')

            analysis_params = {k: v for k, v in combo.items() if k not in strategy_keys}
            strategy_params = {k: v for k, v in combo.items() if k in strategy_keys}
            for svc in AnalysisEngine._services.values():
                if analysis_params:
                    if isinstance(svc.config, dict): svc.config.update(analysis_params)
                    else: svc.config = analysis_params
            for svc in FibStrategy._apps.values():
                if "position_size" in strategy_params: svc.position_size = strategy_params["position_size"]
                if "min_strength" in strategy_params: svc.min_strength = strategy_params["min_strength"]

            await self._reset_state(symbol, interval)
            await hub.dispatch(BacktestProgress(job_id="cli", run_index=idx, total_runs=len(combos), status="running", params=combo))

            result = await hub.execute(RunBacktest(symbol=symbol, interval=interval, warmup_bars=self.warmup_bars))
            metrics = {}
            if result:
                fills = result.get("fills", [])
                account = result.get("account", {})
                initial = account.get("initial_balance", 100000)
                metrics = compute_metrics(fills, initial, account.get("total", initial))
                metrics.pop("equity_curve", None)
            results.append({"params": combo, "result": result, "metrics": metrics})

            await hub.dispatch(BacktestProgress(job_id="cli", run_index=idx, total_runs=len(combos),
                                                status="completed" if result else "failed", params=combo, metrics=metrics))
            log.info(f'[批量回测] [{idx+1}/{len(combos)}] 完成 成交={len(result.get("fills", [])) if result else 0}')

        log.info(f'[批量回测] 全部完成 {len(results)}组')
        return results

    async def _reset_state(self, symbol: str, interval: str):
        for svc in AnalysisEngine._services.values():
            if not svc.db: continue
            await svc.db.delete("checkpoints", symbol=symbol, interval=interval)
            await svc.db.delete("signals", symbol=symbol, interval=interval)
            await svc.db.delete("touches", symbol=symbol, interval=interval)
            await svc.db.delete("retracements", symbol=symbol, interval=interval)

        for svc in FibStrategy._apps.values():
            if hasattr(svc, 'db') and svc.db:
                await svc.db.delete("decisions", symbol=symbol)

        broker = next((s for s in (app._children or []) if getattr(s, 'alias', '') == 'Broker'), None)
        if broker and broker.db:
            await broker.db.clear("fills")
            await broker.db.clear("orders")
            await broker.db.clear("positions")
            broker.exchange.reset()
