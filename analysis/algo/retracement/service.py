"""RetracementService：retracement 算法的唯一状态持有者。

职责：
  1. set_cache / get_cache  — 读写完整计算结果（feature_df + groups 等）
  2. get_all_levels          — 返回所有 FibGroup 的扁平回撤线，供 command 做碰撞/突破
  3. check_touch_with_cooldown — 带冷却的触碰检测（内联 TouchDetector 逻辑）

其他逻辑（invalidate、breakout 判定、touches 记录）均放在 command 中。
"""
import time as _time
from typing import Dict, List, Optional, Tuple
from bollydog.models.service import AppService
from .config import RetracementConfig
from .models import FibGroup


class RetracementService(AppService):
    domain = "timing"
    alias = "RetracementService"
    commands = ["timing.analysis.algo.retracement.command"]

    def __init__(self, config: RetracementConfig = None, **kwargs):
        super().__init__(**kwargs)
        self.engine = None
        self.config = config or RetracementConfig()
        self._cache: Dict[Tuple[str, str], dict] = {}
        self._touch_last: Dict[Tuple[str, str, float, float], float] = {}

    def bind_engine(self, engine): self.engine = engine

    # ── 1. 读写缓存 ──
    def set_cache(self, symbol: str, interval: str, payload: dict):
        """存入 compute_retracement 的完整返回（feature_df, groups, wmap 等）。"""
        self._cache[(symbol, interval)] = payload

    def get_cache(self, symbol: str, interval: str) -> Optional[dict]:
        """取完整缓存，command / engine.save 用。"""
        return self._cache.get((symbol, interval))

    # ── 2. 扁平回撤线 ──
    def get_all_levels(self, symbol: str, interval: str) -> List[Tuple[float, float, str, float]]:
        """返回所有 FibGroup 的 (ratio, price, direction, group_score)，供碰撞/突破检测。"""
        cache = self._cache.get((symbol, interval))
        if not cache: return []
        result = []
        for g in cache.get("groups", []):
            for r, p in g.levels: result.append((r, p, g.direction, g.score))
        return result

    # ── 3. 带冷却的触碰检测 ──
    def check_touch_with_cooldown(self, symbol: str, interval: str,
                                  price: float, levels: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """对 levels 做 tolerance 过滤 + cooldown 去重，返回本次真正触碰的 [(ratio, price)]。"""
        tol, cd = self.config.touch_tolerance, self.config.touch_cooldown_sec
        now = _time.time()
        out = []
        for r, p in levels:
            if abs(price - p) > tol: continue
            key = (symbol, interval, r, p)
            if now - self._touch_last.get(key, 0) >= cd:
                out.append((r, p)); self._touch_last[key] = now
        return out

    async def on_reset(self) -> None:
        self._cache.clear(); self._touch_last.clear()
