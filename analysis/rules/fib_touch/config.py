"""FibTouchConfig — fib_touch Rule 自有配置 schema。

仅包含分析层关注的检测参数，不继承 computation 的配置类。
"""
import os
import tomllib
import logging

log = logging.getLogger(__name__)

DEFAULTS = {
    "touch_tolerance": 0.5,
    "cooldown_bars": 5,
    "approach_lookback": 5,
    "history_lookback_bars": 200,
    "volume_lookback": 20,
    "volume_threshold": 1.5,
    "breakout_tolerance": 1.0,
    "w_consensus": 2.0,
    "w_bounce_rate": 1.5,
    "w_touch_count": 0.1,
    "w_volume": 1.0,
    "w_counter_trend": 0.5,
    "w_candle": 1.0,
    "strong_threshold": 5.0,
    "medium_threshold": 3.5,
    "weak_threshold": 2.0,
    "scan_bars": 0,
}

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")


class FibTouchConfig(dict):
    """dict 子类，支持属性访问。"""

    def __init__(self, **kwargs):
        merged = {**DEFAULTS, **kwargs}
        super().__init__(merged)

    @classmethod
    def from_profile(cls, profile_name: str, overrides: list[str] | None = None) -> "FibTouchConfig":
        """从 profiles/{name}.toml + CLI overrides 构造。"""
        profile_data = _load_profile(profile_name)
        override_data = _parse_overrides(overrides)
        merged = {**DEFAULTS, **profile_data, **override_data}
        cfg = cls(**merged)
        cfg._profile_name = profile_name
        cfg._profile_data = profile_data
        cfg._override_data = override_data
        return cfg

    @property
    def config_source(self) -> dict:
        return {
            "profile": getattr(self, "_profile_name", None),
            "overrides": getattr(self, "_override_data", {}),
        }

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"FibTouchConfig has no key '{key}'")

    def __setattr__(self, key, value):
        self[key] = value


def _load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.toml")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    log.info(f'[fib_touch] 加载 profile: {path}')
    return data


def _parse_overrides(overrides: list[str] | None) -> dict:
    if not overrides:
        return {}
    import json as _json
    result = {}
    for item in overrides:
        if "=" not in item:
            continue
        key, val_str = item.split("=", 1)
        try:
            result[key.strip()] = _json.loads(val_str.strip())
        except (ValueError, _json.JSONDecodeError):
            result[key.strip()] = val_str.strip()
    return result
