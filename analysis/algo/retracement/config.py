"""Retracement 算法参数：swing 拐点 + fib 回撤一体化配置。"""
import os
from dataclasses import dataclass, field, fields
from typing import Dict, List, Tuple

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


@dataclass
class RetracementConfig:
    db_path: str = os.environ.get("TIMING_RETRACEMENT_DB_PATH", "cache/retracement.sqlite")
    # ── swing 拐点 ──
    pivot_windows: List[Tuple[int, int]] = field(default_factory=lambda: [(5, 5), (8, 8)])
    zigzag_thresholds: List[float] = field(default_factory=lambda: [0.05, 0.10])
    regression_windows: List[int] = field(default_factory=lambda: [50, 100])
    weights: Dict[str, float] = field(default_factory=lambda: {
        'pivot_5': 0.5, 'pivot_8': 1.0, 'zigzag_5': 0.5, 'zigzag_10': 1.0,
        'reg_50': 0.5, 'reg_100': 1.0,
    })
    cluster_tolerance_pct: float = 0.005
    min_cluster_conf: float = 0.3
    # ── fib 回撤 ──
    min_leg_span_pct: float = 0.03
    max_ratio_error: float = 0.05
    std_ratios: Tuple[float, ...] = DEFAULT_RATIOS
    top_n: int = 6
    # ── 触碰检测 ──
    touch_tolerance: float = 0.5
    touch_cooldown_sec: float = 60.0
    # ── 突破判定 ──
    breakout_tolerance: float = 1.0
    # ── 趋势腿近期窗口（日K: 60-120, 小时K: 168-336） ──
    recent_bars: int = 90
    # 从最新 bar 往旧数据方向跳过 N 根，不参与分析
    skip_recent: int = 10

    def merge(self, overrides: dict) -> 'RetracementConfig':
        """用 symbol 级覆盖参数创建新 config，db_path 等持久化字段不可覆盖。"""
        if not overrides: return self
        base = {f.name: getattr(self, f.name) for f in fields(self)}
        skip_keys = {'db_path'}
        for k, v in overrides.items():
            if k in skip_keys or k not in base: continue
            base[k] = v
        if isinstance(base.get('pivot_windows'), list):
            base['pivot_windows'] = [tuple(w) if isinstance(w, list) else w for w in base['pivot_windows']]
        if isinstance(base.get('std_ratios'), list):
            base['std_ratios'] = tuple(base['std_ratios'])
        return RetracementConfig(**base)


@dataclass
class TouchConfig:
    """触线信号分析参数：六维特征 + 评分权重 + 信号等级。"""
    # 触碰 / 突破判定
    touch_tolerance: float = 0.5
    breakout_tolerance: float = 1.0
    cooldown_bars: int = 5
    # 特征回溯窗口
    approach_lookback: int = 5
    history_lookback_bars: int = 200
    volume_lookback: int = 20
    volume_threshold: float = 1.5
    # 评分权重
    w_consensus: float = 2.0
    w_bounce_rate: float = 1.5
    w_touch_count: float = 0.1
    w_volume: float = 1.0
    w_counter_trend: float = 0.5
    w_candle: float = 1.0
    # 信号等级阈值
    strong_threshold: float = 5.0
    medium_threshold: float = 3.5
    weak_threshold: float = 2.0
    # 扫描范围（0 = 全量，>0 = 最近 N 根）
    scan_bars: int = 0

    def merge(self, overrides: dict) -> 'TouchConfig':
        if not overrides: return self
        base = {f.name: getattr(self, f.name) for f in fields(self)}
        for k, v in overrides.items():
            if k in base: base[k] = v
        return TouchConfig(**base)
