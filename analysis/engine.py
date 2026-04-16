"""分析引擎 AppService：绑定 RetracementService，不承担算法编排。"""
from timing.analysis.algo.retracement.config import RetracementConfig
from timing.analysis.algo.retracement.service import RetracementService
from timing.analysis.algo import dump_csv
from timing.common.clock import Clock, LiveClock
from bollydog.models.service import AppService


class AnalysisEngine(AppService):
    domain = "timing"
    alias = "AnalysisEngine"
    commands = ["timing.analysis.algo.retracement.command"]

    def __init__(self, clock: Clock = None, config: RetracementConfig = None,
                 protocol=None, router_mapping=None, subscriber=None, **kwargs):
        super().__init__(protocol=protocol, router_mapping=router_mapping, subscribe=subscriber, **kwargs)
        self._load_commands(self.commands)
        self.clock = clock or LiveClock()
        self.retracement = RetracementService(config=config or RetracementConfig())
        self.retracement.bind_engine(self)
        self.add_dependency(self.retracement)

    @property
    def config(self): return self.retracement.config

    def save(self, symbol: str, interval: str):
        cache = self.retracement.get_cache(symbol, interval) or {}
        feature_df = cache.get("feature_df")
        wmap = cache.get("wmap", {})
        cfg = self.config
        if feature_df is not None and not feature_df.empty and wmap:
            pw = "_".join(f"{l}x{r}" for l, r in cfg.pivot_windows)
            zz = "_".join(str(int(t * 100)) for t in cfg.zigzag_thresholds)
            rw = "_".join(str(w) for w in cfg.regression_windows)
            klines = feature_df[["ts", "open", "high", "low", "close", "volume"]].to_dict("records")
            tags = {c: feature_df[c].tolist() for c in wmap.keys() if c in feature_df.columns}
            cols = sorted(tags.keys())
            head = ["ts", "open", "high", "low", "close", "volume"] + cols + ["conf_high", "conf_low"]
            conf_h, conf_l = feature_df["conf_high"].tolist(), feature_df["conf_low"].tolist()
            rows = [tuple([k["ts"], k["open"], k["high"], k["low"], k["close"], k.get("volume", 0)] + [tags[c][i] for c in cols] + [conf_h[i], conf_l[i]]) for i, k in enumerate(klines)]
            dump_csv(f"tmp/{symbol}_swing_tags_p{pw}_z{zz}_r{rw}.csv", head, rows)
            for kind_df_key in ("clusters_high_df", "clusters_low_df"):
                cdf = cache.get(kind_df_key)
                if cdf is not None and not cdf.empty:
                    rows = [(r["kind"], r["center"], r["hit_count"], r["total_conf"], r["last_index"]) for _, r in cdf.iterrows()]
                    dump_csv(f"tmp/{symbol}_swing_clusters.csv", ["kind", "center", "hit_count", "total_conf", "last_index"], rows)
        cache_groups = cache.get("groups", [])
        if cache_groups:
            rows = []
            for i, g in enumerate(cache_groups):
                for r, p in g.levels:
                    rows.append((i, g.direction, round(g.score, 4), g.leg.start_ts, g.leg.end_ts, g.leg.low, g.leg.high, round(g.leg.span_pct, 4), r, round(p, 6)))
            dump_csv(f"tmp/{symbol}_fib_levels.csv", ["group_idx", "direction", "score", "leg_start_ts", "leg_end_ts", "leg_low", "leg_high", "span_pct", "ratio", "price"], rows)

    async def on_reset(self) -> None:
        pass
