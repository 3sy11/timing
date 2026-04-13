"""分析引擎 AppService。编排层：对外暴露 recompute_fib / check_touch，
内部子服务（SwingService / FibService / DetectorService）对 Command 不可见。
protocol(DuckDB) 传递给子服务 command 用于 CSV 落盘。"""
import dataclasses, logging
from dataclasses import fields as dc_fields
from typing import List, Optional, Tuple
from timing.analysis.config import FibConfig
from timing.analysis.models import OnBarForAnalysis, OnCacheIngested
from timing.analysis.algo.swing.service import SwingService
from timing.analysis.algo.swing.command import tag_pivots, tag_zigzag, tag_regression, compute_confidence, cluster_prices
from timing.analysis.algo.fib.service import FibService
from timing.analysis.algo.fib.command import _save_fib_csv
from timing.analysis.algo.detector.service import DetectorService
from timing.analysis.algo import dump_csv
from timing.analysis.types import FibResult, PriceCluster
from timing.common.clock import Clock, LiveClock
from bollydog.models.service import AppService

log = logging.getLogger(__name__)


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"
    commands = ["models", "timing.analysis.algo.swing.command", "timing.analysis.algo.fib.command", "timing.analysis.algo.detector.command"]
    subscriber = {
        "timing.DataEngine.PushBars": OnBarForAnalysis,
        "timing.CacheEngine.OnDataIngested": OnCacheIngested,
    }

    def __init__(self, clock: Clock = None, config: FibConfig = None,
                 protocol=None, router_mapping=None, subscriber=None, **kwargs):
        super().__init__(protocol=protocol, router_mapping=router_mapping, subscribe=subscriber, **kwargs)
        self._load_commands(self.commands)
        self.clock = clock or LiveClock()
        self.config = config or FibConfig()
        self.swing = SwingService()
        self.fib = FibService()
        self.detector = DetectorService(clock=self.clock)
        self.add_dependency(self.swing)
        self.add_dependency(self.fib)
        self.add_dependency(self.detector)

    def recompute_fib(self, symbol: str, interval: str, klines: List[dict]) -> Optional[FibResult]:
        cfg = self.config
        tags = {}
        tags.update(tag_pivots(klines, cfg.pivot_windows))
        tags.update(tag_zigzag(klines, cfg.zigzag_thresholds))
        tags.update(tag_regression(klines, cfg.regression_windows))
        conf_high, conf_low = compute_confidence(tags, cfg.weights)
        ch_raw = cluster_prices(klines, conf_high, "high", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        cl_raw = cluster_prices(klines, conf_low, "low", cfg.cluster_tolerance_pct, cfg.min_cluster_conf)
        self.swing.set_cache(symbol, interval, ch_raw, cl_raw)
        try:
            pw = "_".join(f"{l}x{r}" for l, r in cfg.pivot_windows)
            zz = "_".join(str(int(t * 100)) for t in cfg.zigzag_thresholds)
            rw = "_".join(str(w) for w in cfg.regression_windows)
            tag_cols = sorted(tags.keys())
            header = ["ts", "open", "high", "low", "close", "volume"] + tag_cols + ["conf_high", "conf_low"]
            rows = [tuple([k["ts"], k["open"], k["high"], k["low"], k["close"], k.get("volume", 0)]
                         + [tags[c][i] for c in tag_cols] + [conf_high[i], conf_low[i]])
                    for i, k in enumerate(klines)]
            dump_csv(f"tmp/{symbol}_swing_tags_p{pw}_z{zz}_r{rw}.csv", header, rows)
            all_cl = [dataclasses.asdict(c) for c in ch_raw + cl_raw]
            if all_cl:
                dump_csv(f"tmp/{symbol}_swing_clusters.csv",
                         ["kind", "center", "hit_count", "total_conf", "last_index"],
                         [(c["kind"], c["center"], c["hit_count"], c["total_conf"], c["last_index"]) for c in all_cl])
        except Exception as e:
            log.warning(f'[AnalysisEngine] swing CSV: {e}')
        result = self.fib.compute_and_store(symbol, interval, ch_raw, cl_raw, cfg)
        if result:
            self.detector.reset(symbol, interval)
            try: _save_fib_csv(symbol, result)
            except Exception as e: log.warning(f'[AnalysisEngine] fib CSV: {e}')
        return result

    def check_touch(self, symbol: str, interval: str, bars: List[dict]) -> List[Tuple[float, float, float]]:
        levels = self.fib.get_levels(symbol, interval)
        if not levels: return []
        return self.detector.check_bars(symbol, interval, bars, levels, self.config)

    async def on_reset(self) -> None:
        pass
