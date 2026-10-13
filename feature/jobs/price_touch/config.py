"""price_touch 配置 — 只调当天碰到/价近，不改 Computation 的 ATR 口径。"""
import os
import tomllib
import logging

log = logging.getLogger(__name__)

DEFAULTS = {
    # 基础与触碰判定参数
    "proximity_k": 0.001,
    "neighbor_k": 0.001,
    "scan_bars": 0,
    "emit_if_no_prefit": True,
    # 连续评分与关注区参数
    "attention_zone_k": 0.005,
    "sample_cap": 5,
    "use_group_filter": True,
    "include_pre_effective": False,
    # 线质量评分权重
    "w_hold": 3.0,
    "w_weak": 1.0,
    "w_fit": 1.0,
    "w_cluster": 1.0,
    "w_cluster_conf": 0.5,
    "w_nearby": 1.0,
    "nearby_fib_cap": 3,
    "nearby_cls_cap": 2,
    # 收盘离线用前 atr_period 根真实波幅的中位数，不含当根
    "atr_period": 14,
}

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")


class PriceTouchConfig(dict):
    def __init__(self, **kwargs):
        super().__init__({**DEFAULTS, **kwargs})

    @classmethod
    def from_profile(cls, profile_name: str, overrides: list[str] | None = None) -> "PriceTouchConfig":
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
            raise AttributeError(f"PriceTouchConfig has no key '{key}'")

    def __setattr__(self, key, value):
        self[key] = value


def _load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.toml")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    log.info(f'[price_touch] 加载 profile: {path}')
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
