"""RetracementService — 继承 AnalysisEngine，实现 _warmup / _process_bar。

subscriber 由 AnalysisEngine 统一持有，本类不再声明。
"""
import os, logging, math
from dataclasses import asdict
from typing import Dict, Optional
import pandas as pd
from bollydog.globals import hub
from bollydog.models.service import AppService
from timing.analysis.engine import AnalysisEngine
from .config import RetracementConfig
from .command import ComputeRetracement  # noqa: F401
from .models import TrendLeg, FibGroup

log = logging.getLogger(__name__)

_FEATURE_COLS = ["ts", "high", "low", "close", "conf_high", "conf_low"]
_PASS_KEYS = ("wmap", "legs_found", "legs_kept")


def _df_records(df, cols=None):
    if df is None or df.empty: return []
    recs = (df[cols] if cols else df).to_dict("records")
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()} for r in recs]


def _serialize(result: dict) -> dict:
    out = {"features": _df_records(result.get("feature_df"), _FEATURE_COLS),
           "clusters_high": _df_records(result.get("clusters_high_df")),
           "clusters_low": _df_records(result.get("clusters_low_df")),
           "groups": [{"leg": asdict(g.leg), "levels": g.levels, "score": g.score, "direction": g.direction}
                      for g in result.get("groups", [])]}
    for k in _PASS_KEYS: out[k] = result.get(k, 0 if k != "wmap" else {})
    return out


def _deserialize(data: dict) -> dict:
    out = {"feature_df": pd.DataFrame(data.get("features", [])),
           "clusters_high_df": pd.DataFrame(data.get("clusters_high", [])),
           "clusters_low_df": pd.DataFrame(data.get("clusters_low", [])),
           "groups": [FibGroup(leg=TrendLeg(**gd["leg"]), levels=[tuple(lv) for lv in gd["levels"]],
                               score=gd["score"], direction=gd["direction"]) for gd in data.get("groups", [])]}
    for k in _PASS_KEYS: out[k] = data.get(k, 0 if k != "wmap" else {})
    return out


class RetracementService(AnalysisEngine):
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]
    router_mapping = {"ComputeRetracement": ["POST", "/api/timing/compute_retracement"]}

    def __init__(self, cache_path=None, **kwargs):
        self.config = RetracementConfig()
        super().__init__(cache_path=cache_path, **kwargs)
        self._live: Dict[str, dict] = {}
        self.touch_last: Dict[tuple, float] = {}

    def on_init_dependencies(self):
        if self.protocol: return []
        from bollydog.adapters.composite import CacheLayer
        from bollydog.adapters.memory import SQLiteProtocol
        db_path = os.path.join(self._cache_path, "retracement.sqlite")
        inner = SQLiteProtocol(path=db_path)
        proto = CacheLayer(flush_threshold=1)
        proto.add_dependency(inner)
        log.info(f'[RetracementService] default protocol: SQLite({db_path}) → CacheLayer')
        return [proto]

    async def on_start(self) -> None:
        self._live.clear()
        self.touch_last.clear()
        await super().on_start()

    async def on_started(self) -> None:
        await super().on_started()
        restored = 0
        for key in await self.protocol.keys("retracement:*"):
            serialized = await self.protocol.get(key)
            if serialized:
                self._live[key] = _deserialize(serialized)
                restored += 1
        if restored: log.info(f'[Retracement] on_started recovered {restored} entries')

    def _key(self, symbol: str, interval: str) -> str:
        return f"retracement:{symbol}:{interval}"

    async def set_cache(self, symbol: str, interval: str, result: dict):
        key = self._key(symbol, interval)
        self._live[key] = result
        await self.protocol.set(key, _serialize(result))

    def get_cache(self, symbol: str, interval: str) -> Optional[dict]:
        return self._live.get(self._key(symbol, interval))

    async def _warmup(self, symbol, interval, klines):
        from .algo import compute_retracement
        result = compute_retracement(klines, self.config)
        await self.set_cache(symbol, interval, result)
        log.info(f'[Retracement] _warmup {symbol}/{interval} klines={len(klines)} groups={len(result.get("groups", []))}')

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        close = float(bar.get("close", 0))
        if not close: return {"signals": [], "breakouts": [], "recomputed": False}
        from .touch import compute_consensus_strength, check_breakout
        from .algo import compute_retracement
        cfg = self.config
        cache = self.get_cache(symbol, interval)
        groups = cache.get("groups", []) if cache else []
        now = self.clock.now_sec()
        consensus = compute_consensus_strength(close, groups, cfg=cfg)
        signals = []
        for hit in consensus["hits"]:
            key = (symbol, interval, hit["ratio"], hit["level_price"])
            if now - self.touch_last.get(key, 0) < cfg.touch_cooldown_sec: continue
            self.touch_last[key] = now
            signals.append({"ratio": hit["ratio"], "level_price": hit["level_price"],
                            "touch_price": close, "direction": hit["direction"], "group_idx": hit["group_idx"]})
        broken = check_breakout(close, groups, cfg=cfg)
        recomputed = False
        if broken:
            broken_idx = {b["group_idx"] for b in broken}
            if cache: cache["groups"] = [g for i, g in enumerate(groups) if i not in broken_idx]
            from timing.data.models import GetKlines
            get_cmd = GetKlines(symbol=symbol, interval=interval)
            result = await hub.execute(get_cmd)
            klines = result.state.result() if result and result.state.done() else None
            if klines:
                result = compute_retracement(klines, cfg)
                await self.set_cache(symbol, interval, result)
                recomputed = True
            elif cache:
                await self.set_cache(symbol, interval, cache)
        return {"signals": signals, "breakouts": broken, "recomputed": recomputed}
