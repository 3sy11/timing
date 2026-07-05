"""FibRetracementConfig — 从 dict 按需构造的配置对象。

继承 dict 实现鸭子类型：既支持 cfg.pivot_windows 属性访问，
又兼容 bollydog create_from 的 svc.config = dict 约定。
通过 RetracementConfig(**any_dict) 构造时，未知 key 保留在 dict 中，
缺省 key 自动填充默认值。

支持 Profile 加载：profiles/{name}.toml 覆盖 DEFAULTS，CLI override 再覆盖 profile。
"""
import os
import tomllib
import logging

log = logging.getLogger(__name__)

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

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")


def load_profile(name: str) -> dict:
    """加载 profiles/{name}.toml，返回参数 dict。文件不存在则返回空 dict。"""
    path = os.path.join(PROFILES_DIR, f"{name}.toml")
    if not os.path.isfile(path):
        log.debug(f'[配置] profile 不存在，使用纯 DEFAULTS: {path}')
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    log.info(f'[配置] 加载 profile: {path} ({len(data)} keys)')
    return data


def parse_overrides(overrides: list[str] | None) -> dict:
    """解析 CLI --override 参数列表，如 ["recent_bars=120", "top_n=8"]。"""
    if not overrides:
        return {}
    result = {}
    for item in overrides:
        if "=" not in item:
            log.warning(f'[配置] 忽略无效 override (缺少=): {item}')
            continue
        key, val_str = item.split("=", 1)
        key = key.strip()
        val_str = val_str.strip()
        # 尝试转换类型
        try:
            import json
            result[key] = json.loads(val_str)
        except (json.JSONDecodeError, ValueError):
            result[key] = val_str
    return result


class RetracementConfig(dict):
    """dict 子类，支持属性访问。框架 svc.config = dict 后仍可 cfg.pivot_windows。"""

    def __init__(self, **kwargs):
        merged = {**DEFAULTS, **kwargs}
        super().__init__(merged)

    @classmethod
    def from_profile(cls, profile_name: str, overrides: list[str] | None = None) -> "RetracementConfig":
        """从 profile + overrides 构造配置。优先级：DEFAULTS < profile < overrides。"""
        profile_data = load_profile(profile_name)
        override_data = parse_overrides(overrides)
        merged = {**DEFAULTS, **profile_data, **override_data}
        cfg = cls(**merged)
        cfg._profile_name = profile_name
        cfg._profile_data = profile_data
        cfg._override_data = override_data
        return cfg

    @property
    def config_source(self) -> dict:
        """返回配置来源元信息，用于 manifest 记录。"""
        return {
            "profile": getattr(self, "_profile_name", None),
            "profile_path": os.path.join(PROFILES_DIR, f"{getattr(self, '_profile_name', '')}.toml")
                if getattr(self, "_profile_name", None) else None,
            "overrides": getattr(self, "_override_data", {}),
        }

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"RetracementConfig has no key '{key}'")

    def __setattr__(self, key, value):
        self[key] = value
