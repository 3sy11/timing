"""RetracementService — CacheLayer(SQLiteProtocol) 复合协议。

内存快读 + SQLite 落盘。存储 JSON 可序列化数据，service 层做 ser/deser 边界。
落盘内容：特征权重（ts+conf）、聚合价格线、Fib 回撤线组。
"""
import logging, math
from typing import Dict, List, Optional, Tuple
import pandas as pd
from bollydog.adapters.composite import CacheLayer
from bollydog.adapters.memory import SQLiteProtocol
from bollydog.models.service import AppService
from .config import RetracementConfig
from .command import OnBarReceived, ComputeRetracement
from .models import TrendLeg, FibGroup

log = logging.getLogger(__name__)


def _serialize(result: dict) -> dict:
    """compute_retracement 结果 → JSON 可序列化 dict（落盘用）。"""
    feature_df = result.get("feature_df")
    features = []
    if feature_df is not None and not feature_df.empty:
        for _, row in feature_df[["ts", "high", "low", "close", "conf_high", "conf_low"]].iterrows():
            features.append({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()})
    ch_df, cl_df = result.get("clusters_high_df"), result.get("clusters_low_df")
    clusters_high = ch_df.to_dict("records") if ch_df is not None and not ch_df.empty else []
    clusters_low = cl_df.to_dict("records") if cl_df is not None and not cl_df.empty else []
    groups_out = []
    for g in result.get("groups", []):
        lg = g.leg
        groups_out.append({
            "leg": {"start_idx": lg.start_idx, "end_idx": lg.end_idx, "start_ts": lg.start_ts, "end_ts": lg.end_ts,
                    "low": lg.low, "high": lg.high, "direction": lg.direction, "span_pct": lg.span_pct, "conf_score": lg.conf_score},
            "levels": [[r, p] for r, p in g.levels], "score": g.score, "direction": g.direction,
        })
    wmap = result.get("wmap", {})
    return {"features": features, "clusters_high": clusters_high, "clusters_low": clusters_low,
            "groups": groups_out, "wmap": wmap,
            "legs_found": result.get("legs_found", 0), "legs_kept": result.get("legs_kept", 0)}


def _deserialize(data: dict) -> dict:
    """JSON dict → 包含 DataFrame/FibGroup 的 rich 对象。"""
    feature_df = pd.DataFrame(data.get("features", []))
    clusters_high_df = pd.DataFrame(data.get("clusters_high", []))
    clusters_low_df = pd.DataFrame(data.get("clusters_low", []))
    groups = []
    for gd in data.get("groups", []):
        leg = TrendLeg(**gd["leg"])
        groups.append(FibGroup(leg=leg, levels=[(lv[0], lv[1]) for lv in gd["levels"]],
                               score=gd["score"], direction=gd["direction"]))
    return {"feature_df": feature_df, "clusters_high_df": clusters_high_df, "clusters_low_df": clusters_low_df,
            "wmap": data.get("wmap", {}), "groups": groups,
            "legs_found": data.get("legs_found", 0), "legs_kept": data.get("legs_kept", 0)}


class RetracementService(AppService):
    domain = "timing"
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]
    router_mapping = {"ComputeRetracement": ["POST", "/api/timing/compute_retracement"]}
    subscriber = {"timing.DataEngine.PushBars": OnBarReceived}

    def __init__(self, config: RetracementConfig = None, **kwargs):
        self.config = config or RetracementConfig()
        inner = SQLiteProtocol(path=self.config.db_path)
        proto = CacheLayer(protocol=inner, flush_threshold=1)
        super().__init__(protocol=proto, **kwargs)
        self.add_dependency(proto)
        self._live: Dict[str, dict] = {}
        self.touch_last: Dict[tuple, float] = {}

    # ═══════ 缓存读写（ser/deser 边界） ═══════

    def _key(self, symbol: str, interval: str) -> str:
        return f"retracement:{symbol}:{interval}"

    async def set_cache(self, symbol: str, interval: str, result: dict):
        """序列化 + 写入 CacheLayer（内存+SQLite）。"""
        key = self._key(symbol, interval)
        self._live[key] = result
        await self.protocol.set(key, _serialize(result))

    def get_cache(self, symbol: str, interval: str) -> Optional[dict]:
        return self._live.get(self._key(symbol, interval))

    def get_all_levels(self, symbol: str, interval: str) -> List[Tuple[float, float, str, float]]:
        cache = self.get_cache(symbol, interval)
        if not cache: return []
        return [(r, p, g.direction, g.score) for g in cache.get("groups", []) for r, p in g.levels]

    # ═══════ 生命周期 ═══════

    async def on_start(self) -> None:
        await super().on_start()
        restored = 0
        for key in await self.protocol.keys("retracement:*"):
            serialized = await self.protocol.get(key)
            if serialized:
                self._live[key] = _deserialize(serialized)
                groups = self._live[key].get("groups", [])
                log.info(f'[Retracement] restored {key} groups={len(groups)}')
                restored += 1
        if restored:
            log.info(f'[Retracement] cold-start recovered {restored} entries from SQLite')

    async def on_reset(self) -> None:
        self._live.clear()
        self.touch_last.clear()
