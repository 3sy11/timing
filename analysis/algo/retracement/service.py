"""RetracementService — CacheLayer(SQLiteProtocol) 复合协议。
内存快读 + SQLite 落盘。存储 JSON 可序列化数据，service 层做 ser/deser 边界。
"""
import logging, math
from dataclasses import asdict
from typing import Dict, Optional
import pandas as pd
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.models.service import AppService
from .config import RetracementConfig
from .command import OnBarReceived, ComputeRetracement
from .models import TrendLeg, FibGroup

log = logging.getLogger(__name__)

_FEATURE_COLS = ["ts", "high", "low", "close", "conf_high", "conf_low"]
_PASS_KEYS = ("wmap", "legs_found", "legs_kept")


def _df_records(df, cols=None):
    """DataFrame → list[dict]，NaN → None。"""
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


class RetracementService(AppService):
    domain = "timing"
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]
    router_mapping = {"ComputeRetracement": ["POST", "/api/timing/compute_retracement"]}
    subscriber = {"timing.DataEngine.PushBars": OnBarReceived}

    def __init__(self, config: RetracementConfig = None, clock=None, data_engine=None, **kwargs):
        self.config = config or RetracementConfig()
        self.clock = clock
        self.data_engine = data_engine
        inner = SQLiteProtocol(path=self.config.db_path)
        proto = CacheLayer(protocol=inner, flush_threshold=1)
        super().__init__(protocol=proto, **kwargs)
        self._live: Dict[str, dict] = {}
        self.touch_last: Dict[tuple, float] = {}

    # ═══════ 生命周期 ═══════

    def service_reset(self) -> None:
        """清空运行态 + 按当前 config.db_path 重建协议对象（回测隔离关键）。"""
        self._live.clear()
        self.touch_last.clear()
        inner = SQLiteProtocol(path=self.config.db_path)
        self.protocol = CacheLayer(protocol=inner, flush_threshold=1)
        log.info(f'[Retracement] service_reset db_path={self.config.db_path}')

    async def on_start(self) -> None:
        await super().on_start()
        restored = 0
        for key in await self.protocol.keys("retracement:*"):
            serialized = await self.protocol.get(key)
            if serialized:
                self._live[key] = _deserialize(serialized)
                log.info(f'[Retracement] restored {key} groups={len(self._live[key].get("groups", []))}')
                restored += 1
        if restored:
            log.info(f'[Retracement] cold-start recovered {restored} entries from SQLite')

    # ═══════ 缓存读写 ═══════

    def _key(self, symbol: str, interval: str) -> str:
        return f"retracement:{symbol}:{interval}"

    async def set_cache(self, symbol: str, interval: str, result: dict):
        key = self._key(symbol, interval)
        self._live[key] = result
        await self.protocol.set(key, _serialize(result))

    def get_cache(self, symbol: str, interval: str) -> Optional[dict]:
        return self._live.get(self._key(symbol, interval))

    # ═══════ 分析接口 ═══════

    async def on_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        """单 bar 分析：触碰检测 + 突破判定 + 自动重算。使用 self.config / self.clock / self.data_engine。"""
        close = float(bar.get("close", 0))
        if not close: return {"signals": [], "breakouts": [], "recomputed": False}
        from .touch import compute_consensus_strength, check_breakout
        from .algo import compute_retracement
        cache = self.get_cache(symbol, interval)
        groups = cache.get("groups", []) if cache else []
        now = self.clock.now_sec() if self.clock else 0
        consensus = compute_consensus_strength(close, groups, self.config.touch_tolerance)
        signals = []
        for hit in consensus["hits"]:
            key = (symbol, interval, hit["ratio"], hit["level_price"])
            if self.clock and now - self.touch_last.get(key, 0) < self.config.touch_cooldown_sec: continue
            self.touch_last[key] = now
            signals.append({"ratio": hit["ratio"], "level_price": hit["level_price"],
                            "touch_price": close, "direction": hit["direction"],
                            "group_idx": hit["group_idx"]})
        broken = check_breakout(close, groups, self.config.breakout_tolerance)
        recomputed = False
        if broken:
            broken_idx = {b["group_idx"] for b in broken}
            if cache: cache["groups"] = [g for i, g in enumerate(groups) if i not in broken_idx]
            klines = self.data_engine.get_klines(symbol, interval) if self.data_engine else None
            if klines:
                result = compute_retracement(klines, self.config)
                await self.set_cache(symbol, interval, result)
                recomputed = True
            elif cache:
                await self.set_cache(symbol, interval, cache)
        return {"signals": signals, "breakouts": broken, "recomputed": recomputed}
