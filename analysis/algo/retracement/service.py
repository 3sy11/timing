"""RetracementService — 斐波那契回撤分析子服务。

继承 AnalysisEngine，实现 _warmup / _process_bar。
subscriber 由 TOML 声明（data.DataEngine.PushBars → on_bar）。
缓存状态全部通过 self.protocol 管理，无本地字典。
"""
import logging, math
from dataclasses import asdict
import pandas as pd
from bollydog.globals import hub
from timing.analysis.app import AnalysisEngine
from timing.data.models import GetKlines
from .config import RetracementConfig
from .command import ComputeRetracement  # noqa: F401
from .models import TrendLeg, FibGroup
from .algo import compute_retracement
from .touch import compute_consensus_strength, check_breakout

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

    async def _warmup(self, symbol: str, interval: str, klines: list):
        result = compute_retracement(klines, self.config)
        await self.protocol.set(f"retracement:{symbol}:{interval}", _serialize(result))
        log.info(f'[Retracement] warmup {symbol}/{interval} bars={len(klines)} groups={len(result.get("groups", []))}')

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        close = float(bar.get("close", 0))
        if not close: return {"signals": [], "breakouts": [], "recomputed": False}
        cfg = self.config
        cache_key = f"retracement:{symbol}:{interval}"

        # 读取缓存的回撤计算结果
        raw = await self.protocol.get(cache_key)
        cache = _deserialize(raw) if raw else None
        groups = cache.get("groups", []) if cache else []

        # 检测触碰信号（带 cooldown 去重）
        now = self.clock.now_sec()
        touch_key = f"_touch:{symbol}:{interval}"
        touch_map = await self.protocol.get(touch_key) or {}
        consensus = compute_consensus_strength(close, groups, cfg=cfg)
        signals = []
        for hit in consensus["hits"]:
            tk = f"{hit['ratio']}:{hit['level_price']}"
            if now - touch_map.get(tk, 0) < cfg.touch_cooldown_sec: continue
            touch_map[tk] = now
            signals.append({"ratio": hit["ratio"], "level_price": hit["level_price"],
                            "touch_price": close, "direction": hit["direction"], "group_idx": hit["group_idx"]})
        if signals:
            await self.protocol.set(touch_key, touch_map)

        # 检测突破 → 重新计算回撤结构
        broken = check_breakout(close, groups, cfg=cfg)
        recomputed = False
        if broken:
            broken_idx = {b["group_idx"] for b in broken}
            if cache: cache["groups"] = [g for i, g in enumerate(groups) if i not in broken_idx]
            result = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            klines = result.state.result() if result and result.state.done() else None
            if klines:
                new_result = compute_retracement(klines, cfg)
                await self.protocol.set(cache_key, _serialize(new_result))
                recomputed = True
            elif cache:
                await self.protocol.set(cache_key, _serialize(cache))

        return {"signals": signals, "breakouts": broken, "recomputed": recomputed}
