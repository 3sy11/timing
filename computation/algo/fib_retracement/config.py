"""FibRetracementConfig — 从 dict 按需构造的配置对象。

继承 dict 实现鸭子类型：既支持 cfg.pivot_windows 属性访问，
又兼容 bollydog create_from 的 svc.config = dict 约定。
通过 RetracementConfig(**any_dict) 构造时，未知 key 保留在 dict 中，
缺省 key 自动填充默认值。
"""

DEFAULTS = {
    "pivot_windows": [[5, 5], [8, 8]],
    "zigzag_thresholds": [0.05, 0.10],
    "regression_windows": [50, 100],
    "weights": {"pivot_5": 0.5, "pivot_8": 1.0, "zigzag_5": 0.5, "zigzag_10": 1.0, "reg_50": 0.5, "reg_100": 1.0},
    "cluster_tolerance_pct": 0.005, "min_cluster_conf": 0.3,
    "min_leg_span_pct": 0.03, "max_ratio_error": 0.05,
    "std_ratios": [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0],
    "top_n": 6, "recent_bars": 90, "skip_recent": 10,
    "touch_tolerance": 0.5, "touch_cooldown_sec": 60.0,
    "breakout_tolerance": 1.0, "cooldown_bars": 5,
    "approach_lookback": 5, "history_lookback_bars": 200,
    "volume_lookback": 20, "volume_threshold": 1.5,
    "w_consensus": 2.0, "w_bounce_rate": 1.5, "w_touch_count": 0.1,
    "w_volume": 1.0, "w_counter_trend": 0.5, "w_candle": 1.0,
    "strong_threshold": 5.0, "medium_threshold": 3.5, "weak_threshold": 2.0,
    "scan_bars": 0, "min_bars": 200,
}


class RetracementConfig(dict):
    """dict 子类，支持属性访问。框架 svc.config = dict 后仍可 cfg.pivot_windows。"""

    def __init__(self, **kwargs):
        merged = {**DEFAULTS, **kwargs}
        super().__init__(merged)

    def __getattr__(self, key):
        try: return self[key]
        except KeyError: raise AttributeError(f"RetracementConfig has no key '{key}'")

    def __setattr__(self, key, value):
        self[key] = value
