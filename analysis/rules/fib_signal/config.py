"""fib_signal 配置 — 过滤与评分。入场/止损/过期不在本层。"""
import os
import tomllib
import logging

log = logging.getLogger(__name__)

DEFAULTS = {
    "min_pre_hold_n": 2,
    "min_pre_hold_rate": 0.5,
    "max_pre_weak_share": 0.6,
    "allow_extrapolated": False,
    "require_cluster_aligned": False,
    "w_pre_hold": 3.0,
    "w_pre_weak": 1.0,
    "w_fit_hold": 1.0,
    "w_cluster": 1.0,
    "w_cluster_conf": 0.5,
    "w_nearby_fib": 1.0,
    "nearby_fib_cap": 3,
    "w_nearby_frate": 0.8,
    "w_nearby_cls": 0.5,
    "nearby_cls_cap": 2,
    "min_signal_score": 3.0,
    "debug_mode": False,
}

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")


class FibSignalConfig(dict):
    def __init__(self, **kwargs):
        super().__init__({**DEFAULTS, **kwargs})

    @classmethod
    def from_profile(cls, profile_name: str, overrides: list[str] | None = None) -> "FibSignalConfig":
        profile_data = _load_profile(profile_name)
        override_data = _parse_overrides(overrides)
        cfg = cls(**{**DEFAULTS, **profile_data, **override_data})
        cfg._profile_name = profile_name
        cfg._profile_data = profile_data
        cfg._override_data = override_data
        return cfg

    @property
    def config_source(self) -> dict:
        return {"profile": getattr(self, "_profile_name", None), "overrides": getattr(self, "_override_data", {})}

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"FibSignalConfig has no key '{key}'")

    def __setattr__(self, key, value):
        self[key] = value


def _load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.toml")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    log.info(f'[fib_signal] 加载 profile: {path}')
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
