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

【存储】
  analysis 表 (name="retracement") → data JSON: {features, groups, touches: {level_key: last_ts}}
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
    if df is None or df.empty:
        return []
    recs = (df[cols] if cols else df).to_dict("records")
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()} for r in recs]


def _serialize(result: dict, touches: dict = None) -> dict:
    out = {"features": _df_records(result.get("feature_df"), _FEATURE_COLS),
           "clusters_high": _df_records(result.get("clusters_high_df")),
           "clusters_low": _df_records(result.get("clusters_low_df")),
           "groups": [{"leg": asdict(g.leg), "levels": g.levels, "score": g.score, "direction": g.direction}
                      for g in result.get("groups", [])],
           "touches": touches or {}}
    for k in _PASS_KEYS:
        out[k] = result.get(k, 0 if k != "wmap" else {})
    return out


def _deserialize(data: dict) -> tuple[dict, dict]:
    out = {"feature_df": pd.DataFrame(data.get("features", [])),
           "clusters_high_df": pd.DataFrame(data.get("clusters_high", [])),
           "clusters_low_df": pd.DataFrame(data.get("clusters_low", [])),
           "groups": [FibGroup(leg=TrendLeg(**gd["leg"]), levels=[tuple(lv) for lv in gd["levels"]],
                               score=gd["score"], direction=gd["direction"]) for gd in data.get("groups", [])]}
    for k in _PASS_KEYS:
        out[k] = data.get(k, 0 if k != "wmap" else {})
    touches = data.get("touches", {})
    return out, touches


class RetracementService(AnalysisEngine):
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]
    router_mapping = {"ComputeRetracement": ["POST", "/api/timing/compute_retracement"], "GetSignals": ["GET", "/api/data/signals"]}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def cfg(self) -> RetracementConfig:
        if isinstance(self.config, RetracementConfig):
            return self.config
        return RetracementConfig(**(self.config if isinstance(self.config, dict) else {}))

    async def _warmup(self, symbol: str, interval: str, klines: list):
        result = compute_retracement(klines, self.cfg)
        await self.db.put("analysis", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                       "name": "retracement", "ts": self.clock.now_ms(),
                                       "data": _serialize(result)})
        log.info(f'[回撤分析] 预热完成 {symbol}/{interval} {len(klines)}根K线 → {len(result.get("groups", []))}组结构')

    async def _process_bar(self, symbol: str, interval: str, bar: dict) -> dict:
        close = float(bar.get("close", 0))
        if not close:
            return {"signals": [], "breakouts": [], "recomputed": False}
        cfg = self.cfg

        raw = await self.db.get("analysis", run_id=self.run_id, symbol=symbol, interval=interval, name="retracement")
        if not raw:
            return {"signals": [], "breakouts": [], "recomputed": False}
        cache, touch_map = _deserialize(raw["data"])
        groups = cache.get("groups", [])

        now = self.clock.now_sec()
        consensus = compute_consensus_strength(close, groups, cfg=cfg)
        signals = []
        for hit in consensus["hits"]:
            tk = f"{hit['ratio']}:{hit['level_price']}"
            if now - touch_map.get(tk, 0) < cfg.touch_cooldown_sec:
                continue
            touch_map[tk] = now
            signals.append({"ratio": hit["ratio"], "level_price": hit["level_price"],
                            "touch_price": close, "direction": hit["direction"], "group_idx": hit["group_idx"],
                            "strength": consensus["strength"], "source": "retracement",
                            "ts": int(bar.get("ts", 0))})

        broken = check_breakout(close, groups, cfg=cfg)
        recomputed = False
        if broken:
            broken_idx = {b["group_idx"] for b in broken}
            cache["groups"] = [g for i, g in enumerate(groups) if i not in broken_idx]
            klines = await hub.execute(GetKlines(symbol=symbol, interval=interval))
            if klines:
                new_result = compute_retracement(klines, cfg)
                await self.db.put("analysis", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                               "name": "retracement", "ts": self.clock.now_ms(),
                                               "data": _serialize(new_result, touch_map)})
                recomputed = True
            else:
                await self.db.put("analysis", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                               "name": "retracement", "ts": self.clock.now_ms(),
                                               "data": _serialize(cache, touch_map)})
        elif signals:
            await self.db.put("analysis", {"run_id": self.run_id, "symbol": symbol, "interval": interval,
                                           "name": "retracement", "ts": self.clock.now_ms(),
                                           "data": _serialize(cache, touch_map)})

        return {"signals": signals, "breakouts": broken, "recomputed": recomputed}
