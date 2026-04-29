"""RetracementConfig — 实例化配置，每个子服务持有自己的 config 实例。"""
from dataclasses import dataclass, field


@dataclass
class RetracementConfig:
    # ── algo: swing 拐点 ──
    pivot_windows: list = field(default_factory=lambda: [[5, 5], [8, 8]])
    zigzag_thresholds: list = field(default_factory=lambda: [0.05, 0.10])
    regression_windows: list = field(default_factory=lambda: [50, 100])
    weights: dict = field(default_factory=lambda: {
        'pivot_5': 0.5, 'pivot_8': 1.0, 'zigzag_5': 0.5, 'zigzag_10': 1.0,
        'reg_50': 0.5, 'reg_100': 1.0})
    cluster_tolerance_pct: float = 0.005
    min_cluster_conf: float = 0.3
    # ── algo: fib 回撤 ──
    min_leg_span_pct: float = 0.03
    max_ratio_error: float = 0.05
    std_ratios: list = field(default_factory=lambda: [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0])
    top_n: int = 6
    recent_bars: int = 90
    skip_recent: int = 10
    # ── touch: 触碰检测 ──
    touch_tolerance: float = 0.5
    touch_cooldown_sec: float = 60.0
    breakout_tolerance: float = 1.0
    cooldown_bars: int = 5
    approach_lookback: int = 5
    history_lookback_bars: int = 200
    volume_lookback: int = 20
    volume_threshold: float = 1.5
    # ── touch: 评分权重 ──
    w_consensus: float = 2.0
    w_bounce_rate: float = 1.5
    w_touch_count: float = 0.1
    w_volume: float = 1.0
    w_counter_trend: float = 0.5
    w_candle: float = 1.0
    # ── touch: 信号等级阈值 ──
    strong_threshold: float = 5.0
    medium_threshold: float = 3.5
    weak_threshold: float = 2.0
    scan_bars: int = 0
    # ── 生命周期 ──
    min_bars: int = 200

    def apply_overrides(self, overrides: dict) -> list:
        """覆盖实例属性，返回已应用的 key 列表。"""
        if not overrides: return []
        applied = []
        for k, v in overrides.items():
            if not hasattr(self, k) or k.startswith('_'): continue
            setattr(self, k, v); applied.append(k)
        return applied
