"""BatchBacktest — 批量参数扫描回测命令。"""
import itertools, logging
from typing import Any, ClassVar
from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand
from timing.analysis.app import AnalysisEngine
from timing.strategy.app import FibStrategy
from timing.engine.command import RunBacktest
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
                    if isinstance(svc.config, dict):
                        svc.config.update(analysis_params)
                    else:
                        svc.config = analysis_params
            for svc in FibStrategy._apps.values():
                if "position_size" in strategy_params:
                    svc.position_size = strategy_params["position_size"]
                if "min_strength" in strategy_params:
                    svc.min_strength = strategy_params["min_strength"]

            result = await hub.execute(RunBacktest(symbol=symbol, interval=interval, warmup_bars=self.warmup_bars))
            metrics = {}
            if result:
                fills_count = result.get("fills_count", 0)
                account = result.get("account", {})
                initial = account.get("initial_balance", 100000)
                metrics = compute_metrics([], initial, account.get("total", initial))
            results.append({"params": combo, "result": result, "metrics": metrics})
            log.info(f'[批量回测] [{idx+1}/{len(combos)}] 完成 run_id={result.get("run_id") if result else "N/A"}')

        log.info(f'[批量回测] 全部完成 {len(results)}组')
        return results
