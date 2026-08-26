"""PriceTouchConfig — v3 价格线触碰检测配置。"""
import os, tomllib, logging

log = logging.getLogger(__name__)

DEFAULTS = {
    "proximity_k": 0.1,           # 检测半径 = price × proximity_k × 0.01 (即0.1%)
    "w_consensus": 0.7,           # consensus 归一化权重
    "w_proximity": 0.3,           # proximity 权重
    "scan_bars": 0,
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
        try: return self[key]
        except KeyError: raise AttributeError(f"PriceTouchConfig has no key '{key}'")

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
        try: result[key.strip()] = _json.loads(val_str.strip())
        except (ValueError, _json.JSONDecodeError): result[key.strip()] = val_str.strip()
    return result
