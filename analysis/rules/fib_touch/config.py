"""FibTouchConfig v2 — 纯定量proximity测量配置。

proximity_k: 感知半径(替代原tolerance_k), max_distance = leg_range × proximity_k
权重参数: w_proximity, w_bounce, w_volume, w_consensus, w_ratio
"""
import os
import tomllib
import logging

log = logging.getLogger(__name__)

DEFAULTS = {
    "proximity_k": 0.15,          # 感知半径: max_distance = leg_range × 0.15
    "min_leg_range_pct": 0.02,    # 窄腿过滤
    "cooldown_bars": 3,           # 同一线冷却期
    "history_lookback_bars": 200, # bounce_rate回看窗口
    "volume_lookback": 20,        # volume均量回看
    "volume_cap": 3.0,            # volume_ratio归一化上限
    "scan_bars": 0,               # 0=全量扫描
    # 衍生score权重
    "w_proximity": 2.0,
    "w_bounce": 1.5,
    "w_volume": 0.5,
    "w_consensus": 1.0,
    "w_ratio": 0.5,
}

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")


class FibTouchConfig(dict):
    """dict子类，支持属性访问。"""
    def __init__(self, **kwargs):
        merged = {**DEFAULTS, **kwargs}
        super().__init__(merged)

    @classmethod
    def from_profile(cls, profile_name: str, overrides: list[str] | None = None) -> "FibTouchConfig":
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
        return {"profile": getattr(self, "_profile_name", None), "overrides": getattr(self, "_override_data", {})}

    def __getattr__(self, key):
        try: return self[key]
        except KeyError: raise AttributeError(f"FibTouchConfig has no key '{key}'")

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
        try: result[key.strip()] = _json.loads(val_str.strip())
        except (ValueError, _json.JSONDecodeError): result[key.strip()] = val_str.strip()
    return result
