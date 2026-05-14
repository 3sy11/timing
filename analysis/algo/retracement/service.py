"""
RetracementService — 斐波那契回撤分析子服务。

【继承关系】
  RetracementService → AnalysisEngine → AppService
  只需实现 _warmup 和 _process_bar 两个方法

【核心逻辑】
  _warmup：用前 N 根 K 线计算初始的回撤结构（pivot点 → 腿 → 组 → levels）
  _process_bar：对每根新 bar 做两件事：
    1. 检测"触碰" — 价格是否靠近关键回撤位 → 产出信号
    2. 检测"突破" — 价格是否穿过了某组结构 → 重新计算回撤

【缓存说明】
  "retracement:{symbol}:{interval}"  — 完整的回撤计算结果（含 groups、features）
  "_touch:{symbol}:{interval}"       — 触碰去重 map（每个 level 的上次触碰时间）
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


# ──────────────── 序列化/反序列化（缓存用） ────────────────

def _df_records(df, cols=None):
    """DataFrame → list[dict]，NaN 转 None。"""
    if df is None or df.empty: return []
    recs = (df[cols] if cols else df).to_dict("records")
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()} for r in recs]


def _serialize(result: dict) -> dict:
    """把 compute_retracement 的结果转成可 JSON 序列化的 dict（存缓存用）。"""
    out = {"features": _df_records(result.get("feature_df"), _FEATURE_COLS),
           "clusters_high": _df_records(result.get("clusters_high_df")),
           "clusters_low": _df_records(result.get("clusters_low_df")),
           "groups": [{"leg": asdict(g.leg), "levels": g.levels, "score": g.score, "direction": g.direction}
                      for g in result.get("groups", [])]}
    for k in _PASS_KEYS: out[k] = result.get(k, 0 if k != "wmap" else {})
    return out


def _deserialize(data: dict) -> dict:
    """把缓存中的 dict 还原成 compute_retracement 格式的对象。"""
    out = {"feature_df": pd.DataFrame(data.get("features", [])),
           "clusters_high_df": pd.DataFrame(data.get("clusters_high", [])),
           "clusters_low_df": pd.DataFrame(data.get("clusters_low", [])),
           "groups": [FibGroup(leg=TrendLeg(**gd["leg"]), levels=[tuple(lv) for lv in gd["levels"]],
                               score=gd["score"], direction=gd["direction"]) for gd in data.get("groups", [])]}
    for k in _PASS_KEYS: out[k] = data.get(k, 0 if k != "wmap" else {})
    return out


# ──────────────── 服务实现 ────────────────

class RetracementService(AnalysisEngine):
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]
    router_mapping = {"ComputeRetracement": ["POST", "/api/timing/compute_retracement"]}

    def __init__(self, cache_path=None, **kwargs):
        super().__init__(cache_path=cache_path, **kwargs)

    @property
    def cfg(self) -> RetracementConfig:
        """兼容两种 config 来源：TOML create_from 设的 dict / BacktestApp 覆盖的 dict。"""
        if isinstance(self.config, RetracementConfig): return self.config
        return RetracementConfig(**(self.config if isinstance(self.config, dict) else {}))

    async def _warmup(self, symbol: str, interval: str, klines: list):
        """用历史 K 线计算完整的回撤结构并缓存。"""
        result = compute_retracement(klines, self.cfg)
        await self.protocol.set(f"retracement:{symbol}:{interval}", _serialize(result))
        log.info(f'[回撤分析] 预热完成 {symbol}/{interval} {len(klines)}根K线 → {len(result.get("groups", []))}组结构')

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        """
        处理单根 bar：
        1. 读缓存的回撤结构
        2. 检测是否触碰关键位 → 产出信号
        3. 检测是否突破结构 → 重新计算
        """
        close = float(bar.get("close", 0))
        if not close: return {"signals": [], "breakouts": [], "recomputed": False}
        cfg = self.cfg
        cache_key = f"retracement:{symbol}:{interval}"

        # ① 从缓存读取之前计算好的回撤结构
        raw = await self.protocol.get(cache_key)
        cache = _deserialize(raw) if raw else None
        groups = cache.get("groups", []) if cache else []

        # ② 检测触碰：价格是否靠近某个关键回撤位
        now = self.clock.now_sec()
        touch_key = f"_touch:{symbol}:{interval}"
        touch_map = await self.protocol.get(touch_key) or {}
        consensus = compute_consensus_strength(close, groups, cfg=cfg)
        signals = []
        for hit in consensus["hits"]:
            # cooldown 去重：同一个 level 短时间内不重复发信号
            tk = f"{hit['ratio']}:{hit['level_price']}"
            if now - touch_map.get(tk, 0) < cfg.touch_cooldown_sec: continue
            touch_map[tk] = now
            signals.append({"ratio": hit["ratio"], "level_price": hit["level_price"],
                            "touch_price": close, "direction": hit["direction"], "group_idx": hit["group_idx"],
                            "strength": consensus["strength"]})
        if signals:
            await self.protocol.set(touch_key, touch_map)

        # ③ 检测突破：价格穿过了某组结构的边界 → 该组失效，需要重算
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
