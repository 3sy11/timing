"""从 result.parquet + K 线加工线级衍生表：反弹、共识与同伴质量。"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


DEFAULT_HISTORY_BARS = 0  # 0 = 生效前全部可用 K 线
DEFAULT_TOUCH_TOLERANCE_PCT = 0.001
DEFAULT_CONSENSUS_TOLERANCE_PCT = 0.001

_REACTION_KEYS = (
    "touch_bar_count", "bounce_from_above", "bounce_from_below",
    "bounce_count", "bounce_rate",
)
_CONSENSUS_KEYS = (
    "consensus_group_count", "consensus_line_count",
    "peer_lifecycle_bounce_rate_mean", "peer_lifecycle_bounce_rate_min",
    "peer_aligned_ratio", "peer_conf_sum",
)
_ID_COLS = [
    "effective_ts", "multiplier", "direction", "leg_low", "leg_high", "ratio", "price",
    "nearest_cluster_center", "nearest_cluster_conf", "is_cluster_aligned",
    "invalidated_ts", "fib_score",
]

LINE_FACTOR_COLUMNS = [
    *_ID_COLS,
    "t_end",
    *[f"fib_historical_{key}" for key in _REACTION_KEYS],
    *[f"fib_lifecycle_{key}" for key in _REACTION_KEYS],
    *[f"pl_historical_{key}" for key in _REACTION_KEYS],
    *[f"pl_lifecycle_{key}" for key in _REACTION_KEYS],
    *[f"fib_{key}" for key in _CONSENSUS_KEYS],
    *[f"pl_{key}" for key in _CONSENSUS_KEYS],
]

_ZH_ID_COLUMNS = {
    "effective_ts": "生效时间",
    "multiplier": "时间尺度倍数",
    "direction": "斐波那契方向",
    "leg_low": "波段低点",
    "leg_high": "波段高点",
    "ratio": "斐波那契比例",
    "price": "斐波那契线价格",
    "nearest_cluster_center": "最近聚类中心价格",
    "nearest_cluster_conf": "最近聚类中心置信度",
    "is_cluster_aligned": "是否对齐聚类中心",
    "invalidated_ts": "失效时间",
    "fib_score": "斐波那契拟合得分",
    "t_end": "统计截止时间",
}
_ZH_METRIC_SUFFIXES = {
    "consensus_group_count": "共识组数",
    "consensus_line_count": "共识线数",
    "peer_lifecycle_bounce_rate_mean": "邻居组存活期平均反弹率",
    "peer_lifecycle_bounce_rate_min": "邻居组存活期最低反弹率",
    "peer_aligned_ratio": "邻居组聚类对齐比例",
    "peer_conf_sum": "邻居组置信度合计",
}
LINE_FACTOR_ZH_COLUMNS = {
    **_ZH_ID_COLUMNS,
    **{
        f"{prefix}{window}_{suffix}": f"{zh_prefix}{window_zh}{reaction_zh}"
        for prefix, zh_prefix in (("fib_", "斐波那契线_"), ("pl_", "聚类中心线_"))
        for window, window_zh in (("historical", "历史结构"), ("lifecycle", "存活期"))
        for suffix, reaction_zh in {
            "touch_bar_count": "触碰K线数",
            "bounce_from_above": "上方反弹次数",
            "bounce_from_below": "下方反弹次数",
            "bounce_count": "反弹总次数",
            "bounce_rate": "反弹率",
        }.items()
    },
    **{
        f"{prefix}{suffix}": f"{zh_prefix}{_ZH_METRIC_SUFFIXES[suffix]}"
        for prefix, zh_prefix in (("fib_", "斐波那契线_"), ("pl_", "聚类中心线_"))
        for suffix in _CONSENSUS_KEYS
    },
}


def to_chinese_line_factor(df: pd.DataFrame) -> pd.DataFrame:
    """将内部英文 schema 转换为对外交付的中文 schema。"""
    missing = [col for col in LINE_FACTOR_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"line factor 缺少字段: {missing}")
    return df.loc[:, LINE_FACTOR_COLUMNS].rename(columns=LINE_FACTOR_ZH_COLUMNS)


def _group_key(row) -> tuple:
    return (int(row["effective_ts"]), int(row["multiplier"]), str(row["direction"]),
            round(float(row["leg_low"]), 4), round(float(row["leg_high"]), 4))


def _empty_bounce() -> dict:
    out = {key: 0 for key in _REACTION_KEYS if not key.endswith("rate")}
    out["bounce_rate"] = None
    return out


def _rate(bounce, touch):
    if touch <= 0:
        return None
    return round(bounce / touch, 4)


def _validate_parameters(history_bars: int, touch_tolerance_pct: float,
                         consensus_tolerance_pct: float) -> None:
    if int(history_bars) < 0:
        raise ValueError("line_factor_history_bars 不能小于 0")
    if float(touch_tolerance_pct) < 0:
        raise ValueError("line_factor_touch_tolerance_pct 不能小于 0")
    if float(consensus_tolerance_pct) < 0:
        raise ValueError("line_factor_consensus_tolerance_pct 不能小于 0")


def _reaction_counts(highs, lows, closes, i0: int, i1: int, price: float,
                     touch_tolerance_pct: float) -> dict:
    full = _scan_bounce(highs, lows, closes, i0, i1, price, touch_tolerance_pct)
    return {
        "touch_bar_count": full[0],
        "bounce_from_above": full[1],
        "bounce_from_below": full[2],
        "bounce_count": full[1] + full[2],
        "bounce_rate": _rate(full[1] + full[2], full[0]),
    }


def _scan_bounce(highs, lows, closes, i0: int, i1: int, price: float,
                 touch_tolerance_pct: float) -> tuple:
    if i1 <= i0:
        return 0, 0, 0
    radius = price * touch_tolerance_pct
    in_band = (lows[i0:i1] <= price + radius) & (highs[i0:i1] >= price - radius)
    touch = int(in_band.sum())
    above = below = 0
    for i in np.flatnonzero(in_band) + i0:
        if i == 0 or i + 1 >= len(closes):
            continue
        prev, cur, nxt = closes[i - 1], closes[i], closes[i + 1]
        if prev > price and nxt > cur:
            above += 1
        elif prev < price and nxt < cur:
            below += 1
    return touch, above, below


def _window_indices(ts: np.ndarray, effective_ts: int, invalidated_ts, last_ts: int) -> tuple[int, int]:
    i0 = int(np.searchsorted(ts, effective_ts, side="left"))
    if invalidated_ts is not None and not (isinstance(invalidated_ts, float) and math.isnan(invalidated_ts)):
        i1 = int(np.searchsorted(ts, int(invalidated_ts), side="left"))
    else:
        i1 = int(np.searchsorted(ts, last_ts, side="right"))
    return i0, i1


def _t_end(invalidated_ts, last_ts: int) -> int:
    if invalidated_ts is None or (isinstance(invalidated_ts, float) and math.isnan(invalidated_ts)):
        return last_ts
    return int(invalidated_ts)


def _prefix(data: dict, prefix: str) -> dict:
    return {f"{prefix}{key}": value for key, value in data.items()}


def _pick_representative(lines: list[dict], p: float, price_field: str) -> Optional[dict]:
    inner_aligned = [line for line in lines if line.get("is_cluster_aligned")
                     and 0.001 < float(line["ratio"]) < 0.999]
    pool = inner_aligned or lines
    if not pool:
        return None
    return min(pool, key=lambda line: abs(float(line[price_field]) - p))


def build_line_factors(result_df: pd.DataFrame, klines_df: pd.DataFrame,
                       history_bars: int = DEFAULT_HISTORY_BARS,
                       touch_tolerance_pct: float = DEFAULT_TOUCH_TOLERANCE_PCT,
                       consensus_tolerance_pct: float = DEFAULT_CONSENSUS_TOLERANCE_PCT) -> pd.DataFrame:
    """构建英文内部 schema；写盘前调用 :func:`to_chinese_line_factor`。"""
    _validate_parameters(history_bars, touch_tolerance_pct, consensus_tolerance_pct)
    if result_df.empty or klines_df.empty:
        return pd.DataFrame(columns=LINE_FACTOR_COLUMNS)

    kdf = klines_df.sort_values("ts").reset_index(drop=True)
    ts = kdf["ts"].to_numpy(dtype=np.int64)
    highs = kdf["high"].to_numpy(dtype=np.float64)
    lows = kdf["low"].to_numpy(dtype=np.float64)
    closes = kdf["close"].to_numpy(dtype=np.float64)
    last_ts = int(ts[-1])
    rows = result_df.to_dict("records")
    for i, rec in enumerate(rows):
        rec["_idx"] = i

    by_group: dict[tuple, list[int]] = {}
    for i, rec in enumerate(rows):
        by_group.setdefault(_group_key(rec), []).append(i)

    line_windows: dict[int, tuple[int, int]] = {}
    group_meta = []
    for key, idxs in by_group.items():
        sample = rows[idxs[0]]
        i0, i1 = _window_indices(ts, int(sample["effective_ts"]), sample.get("invalidated_ts"), last_ts)
        for idx in idxs:
            line_windows[idx] = (i0, i1)
        group_meta.append({
            "key": key,
            "multiplier": int(sample["multiplier"]),
            "direction": str(sample["direction"]),
            "lines": [rows[idx] for idx in idxs],
            "i0": i0,
            "i1": i1,
        })

    bounce_by_price: dict[tuple, dict] = {}

    def line_reaction(idx: int, kind: str, window: str, cutoff_i1: int = None) -> dict:
        rec = rows[idx]
        i0, own_i1 = line_windows[idx]
        if window == "historical":
            i1 = i0
            i0 = max(0, i1 - history_bars) if history_bars else 0
        elif window == "lifecycle":
            i1 = min(own_i1, cutoff_i1 if cutoff_i1 is not None else own_i1)
        else:
            raise ValueError(f"未知反应窗口: {window}")
        if kind == "fib":
            target, cache_kind = float(rec["price"]), "f"
        else:
            target = rec.get("nearest_cluster_center")
            if target is None or (isinstance(target, float) and math.isnan(target)):
                return {key: None for key in _REACTION_KEYS}
            target, cache_kind = float(target), "p"
        cache_key = (i0, i1, cache_kind, round(target, 2), touch_tolerance_pct)
        if cache_key not in bounce_by_price:
            bounce_by_price[cache_key] = (
                _reaction_counts(highs, lows, closes, i0, i1, target, touch_tolerance_pct)
                if i1 > i0 else _empty_bounce()
            )
        return bounce_by_price[cache_key]

    own_bounce_cache = {}
    for idx in range(len(rows)):
        _, own_i1 = line_windows[idx]
        own_bounce_cache[idx] = {
            **_prefix(line_reaction(idx, "fib", "historical"), "fib_historical_"),
            **_prefix(line_reaction(idx, "fib", "lifecycle", own_i1), "fib_lifecycle_"),
            **_prefix(line_reaction(idx, "pl", "historical"), "pl_historical_"),
            **_prefix(line_reaction(idx, "pl", "lifecycle", own_i1), "pl_lifecycle_"),
        }

    living_cache: dict[int, list] = {}
    out_rows = []
    for idx, rec in enumerate(rows):
        _, subject_i1 = line_windows[idx]
        if subject_i1 not in living_cache:
            # i1 是统计区间右开边界：同一时刻失效的组，失效前仍属于同一截面。
            living_cache[subject_i1] = [
                group for group in group_meta if group["i0"] < subject_i1 <= group["i1"]
            ]
        living = living_cache[subject_i1]
        self_key = _group_key(rec)
        fib_cons = _consensus_block(
            living, self_key, float(rec["price"]), "fib", line_reaction,
            subject_i1, consensus_tolerance_pct,
        )
        pl = rec.get("nearest_cluster_center")
        has_pl = pl is not None and not (isinstance(pl, float) and math.isnan(pl))
        pl_cons = (
            _consensus_block(living, self_key, float(pl), "pl", line_reaction,
                             subject_i1, consensus_tolerance_pct)
            if has_pl else _null_consensus("pl")
        )
        ident = {col: rec[col] for col in _ID_COLS if col in rec}
        ident["t_end"] = _t_end(rec.get("invalidated_ts"), last_ts)
        out_rows.append({**ident, **own_bounce_cache[idx], **fib_cons, **pl_cons})
    return pd.DataFrame(out_rows, columns=LINE_FACTOR_COLUMNS)


def _null_consensus(kind: str) -> dict:
    return {f"{kind}_{key}": None for key in _CONSENSUS_KEYS}


def _consensus_block(living, self_key, p: float, kind: str, line_reaction,
                     subject_i1: int, consensus_tolerance_pct: float) -> dict:
    radius = p * consensus_tolerance_pct
    price_field = "price" if kind == "fib" else "nearest_cluster_center"
    matched, self_lines, line_count = [], [], 0
    for group in living:
        if group["key"] == self_key:
            self_lines = group["lines"]
            continue
        hit = []
        for line in group["lines"]:
            q = line.get(price_field)
            if q is None or (isinstance(q, float) and math.isnan(q)):
                continue
            if abs(float(q) - p) <= radius:
                hit.append(line)
        if hit:
            line_count += len(hit)
            matched.append((group, hit))

    self_in_band = sum(
        1 for line in self_lines
        if line.get(price_field) is not None
        and not (isinstance(line.get(price_field), float) and math.isnan(line[price_field]))
        and abs(float(line[price_field]) - p) <= radius
    )
    line_count += max(self_in_band, 1)

    peer_rates, aligned, confs = [], [], []
    for group, hit in matched:
        rep = _pick_representative(hit, p, price_field)
        if rep is None:
            continue
        rate = line_reaction(rep["_idx"], kind, "lifecycle", subject_i1).get("bounce_rate")
        if rate is not None and not (isinstance(rate, float) and math.isnan(rate)):
            peer_rates.append(float(rate))
        aligned.append(1.0 if rep.get("is_cluster_aligned") else 0.0)
        conf = rep.get("nearest_cluster_conf")
        if conf is not None and not (isinstance(conf, float) and math.isnan(conf)):
            confs.append(float(conf))

    prefix = f"{kind}_"
    return {
        f"{prefix}consensus_group_count": 1 + len(matched),
        f"{prefix}consensus_line_count": line_count,
        f"{prefix}peer_lifecycle_bounce_rate_mean": round(sum(peer_rates) / len(peer_rates), 4) if peer_rates else None,
        f"{prefix}peer_lifecycle_bounce_rate_min": round(min(peer_rates), 4) if peer_rates else None,
        f"{prefix}peer_aligned_ratio": round(sum(aligned) / len(aligned), 4) if aligned else None,
        f"{prefix}peer_conf_sum": round(sum(confs), 4) if confs else None,
    }
