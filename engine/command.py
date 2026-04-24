"""RunBacktest — 简化版回测命令，直接使用 BacktestApp 的服务树。"""
import logging
from typing import Any, ClassVar
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.engine.evaluate import build_report

log = logging.getLogger(__name__)


class RunBacktest(BaseCommand):
    """restart 分析服务 → 全量计算 → 逐 bar on_bar → 评估报告。"""
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    symbol: str = ""
    interval: str = ""
    warmup_bars: int = 200
    recompute_interval: int = 50

    async def __call__(self, *args, **kwargs) -> Any:
        bt_app = app
        klines = bt_app.data.get_klines(self.symbol, self.interval)
        n = len(klines)
        if n <= self.warmup_bars:
            log.warning(f'[Backtest] klines({n}) <= warmup_bars({self.warmup_bars})'); return None
        svc = bt_app.analysis.retracement
        await svc.restart()
        overrides = bt_app.data.get_symbol_config(self.symbol, self.interval) or {}
        if overrides: svc.config = svc.config.merge(overrides)
        from timing.analysis.algo.retracement.algo import compute_retracement
        result = compute_retracement(klines[:self.warmup_bars], svc.config)
        await svc.set_cache(self.symbol, self.interval, result)
        log.info(f'[Backtest] start {self.symbol}/{self.interval} klines={n} warmup={self.warmup_bars} groups={len(result.get("groups", []))}')
        all_signals, all_breakouts = [], []
        bars_since_recompute = 0
        for i in range(self.warmup_bars, n):
            bar = klines[i]
            bt_app.clock.set_time_ms(int(bar["ts"]))
            r = await svc.on_bar(self.symbol, self.interval, bar)
            for sig in r["signals"]:
                sig.update({"bar_idx": i, "ts": int(bar["ts"])})
                all_signals.append(sig)
            for brk in r["breakouts"]:
                brk.update({"bar_idx": i, "ts": int(bar["ts"]), "close": float(bar["close"])})
                all_breakouts.append(brk)
            if r.get("recomputed"): bars_since_recompute = 0
            else: bars_since_recompute += 1
            if self.recompute_interval > 0 and bars_since_recompute >= self.recompute_interval:
                res = compute_retracement(klines[:i + 1], svc.config)
                await svc.set_cache(self.symbol, self.interval, res)
                bars_since_recompute = 0
        report = build_report(klines, all_signals, all_breakouts, self.warmup_bars, bt_app.analysis_dir)
        bt_app.set_result(self.symbol, self.interval, report)
        log.info(f'[Backtest] done {self.symbol}/{self.interval} signals={len(all_signals)} breakouts={len(all_breakouts)} hit_rate={report["metrics"].get("hit_rate", 0)}')
        return report
