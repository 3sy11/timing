"""从 result.parquet + klines 加工触碰事件事实表 line_events。

一行 = 一条 Fib/聚类中心目标价，在拟合前或拟合窗内的一次已完成触碰。
只打 ATR 口径 outcome_atr。存活期不写事件。期末仍未离开触碰带的进入不入库。
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Optional

import numpy as np
import pandas as pd

EVENT_PARAM_KEYS = (
    "event_touch_k",
    "event_atr_period",
    "event_atr_band_k",
    "event_atr_horizon",
    "event_atr_threshold",
)

EVENT_DEFAULTS = {
    "event_touch_k": 0.001,
    "event_atr_period": 14,
    "event_atr_band_k": 0.25,
    "event_atr_horizon": 5,
    "event_atr_threshold": 0.5,
}

EVENT_COLS = [
    "compute_id", "symbol", "interval",
    "effective_ts", "multiplier", "direction", "leg_low", "leg_high", "ratio",
    "fib_group_id",
    "target_kind", "target_price", "period",
    "event_id", "entered_ts", "completed_ts", "approach",
    "touch_bar_count",
    "atr", "mfe_atr", "mae_atr", "close_back", "outcome_atr", "atr_bars",
]


def resolve_event_params(cfg: Any = None) -> dict:
    out = dict(EVENT_DEFAULTS)
    if cfg is None:
        return out
    for k in EVENT_PARAM_KEYS:
        if isinstance(cfg, dict) and k in cfg:
            out[k] = cfg[k]
        elif hasattr(cfg, k):
            try:
                out[k] = getattr(cfg, k)
            except AttributeError:
                pass
    return out


def fib_group_id(effective_ts, multiplier, direction, leg_low, leg_high) -> str:
    raw = f"{int(effective_ts)}|{int(multiplier)}|{direction}|{round(float(leg_low), 4)}|{round(float(leg_high), 4)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _event_id(group_id: str, ratio, target_kind: str, period: str, entered_ts: int) -> str:
    raw = f"{group_id}|{round(float(ratio), 4)}|{target_kind}|{period}|{int(entered_ts)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _has_price(v) -> bool:
    if v is None:
        return False
    try:
        return not (isinstance(v, float) and math.isnan(v))
    except TypeError:
        return True


def _atr_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    n = len(closes)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    if n > 1:
        prev = closes[:-1]
        tr[1:] = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - prev), np.abs(lows[1:] - prev)),
        )
    atr = np.full(n, np.nan)
    csum = np.cumsum(tr)
    for i in range(1, n):
        start = max(0, i - period)
        prev_sum = csum[start - 1] if start > 0 else 0.0
        atr[i] = (csum[i - 1] - prev_sum) / (i - start)
    return atr


def _band_radius(price: float, atr_val: float, touch_k: float, atr_band_k: float) -> float:
    floor = price * touch_k
    if atr_band_k > 0 and atr_val == atr_val and atr_val > 0:
        return max(floor, atr_band_k * atr_val)
    return floor


def _scan_period(
    ts, highs, lows, closes, atr, i0: int, i1: int, price: float, params: dict,
) -> list[dict]:
    if i1 <= i0:
        return []
    touch_k = float(params["event_touch_k"])
    atr_band_k = float(params["event_atr_band_k"])
    horizon = int(params["event_atr_horizon"])
    thr = float(params["event_atr_threshold"])

    r_slice = np.array([
        _band_radius(price, float(atr[i0 + i]), touch_k, atr_band_k) for i in range(i1 - i0)
    ], dtype=np.float64)
    in_band = (lows[i0:i1] <= price + r_slice) & (highs[i0:i1] >= price - r_slice)
    events = []
    i = 0
    n = i1 - i0
    while i < n:
        if not in_band[i]:
            i += 1
            continue
        enter = i
        j = i + 1
        while j < n and in_band[j]:
            j += 1
        if j >= n:
            break
        abs_enter = i0 + enter
        if abs_enter > 0:
            prev = float(closes[abs_enter - 1])
            approach = "from_above" if prev > price else "from_below"
        else:
            approach = "from_above" if float(closes[abs_enter]) >= price else "from_below"
        atr_e = float(atr[abs_enter]) if abs_enter < len(atr) else float("nan")
        atr_end = min(abs_enter + horizon, i1)
        atr_bars = atr_end - abs_enter
        completed = atr_end - 1 if atr_bars > 0 else i0 + j
        mfe = mae = 0.0
        outcome_atr = None
        close_back = None
        mfe_atr = mae_atr = None
        if atr_bars > 0:
            hit = None
            for k in range(abs_enter, atr_end):
                if approach == "from_above":
                    fav = float(highs[k]) - price
                    adv = price - float(lows[k])
                else:
                    fav = price - float(lows[k])
                    adv = float(highs[k]) - price
                mfe = max(mfe, fav)
                mae = max(mae, adv)
                if atr_e == atr_e and atr_e > 0 and hit is None:
                    mfe_hit = mfe / atr_e >= thr
                    mae_hit = mae / atr_e >= thr
                    if mfe_hit and mae_hit:
                        hit = "break" if mae >= mfe else "bounce"
                    elif mae_hit:
                        hit = "break"
                    elif mfe_hit:
                        hit = "bounce"
            last_c = float(closes[atr_end - 1])
            close_back = bool(
                (approach == "from_above" and last_c > price)
                or (approach == "from_below" and last_c < price)
            )
            if atr_e == atr_e and atr_e > 0:
                mfe_atr = round(mfe / atr_e, 4)
                mae_atr = round(mae / atr_e, 4)
                outcome_atr = hit if hit else "weak"
        events.append({
            "entered_ts": int(ts[abs_enter]),
            "completed_ts": int(ts[completed]),
            "approach": approach,
            "touch_bar_count": int(j - enter),
            "atr": round(atr_e, 4) if atr_e == atr_e else None,
            "mfe_atr": mfe_atr,
            "mae_atr": mae_atr,
            "close_back": close_back,
            "outcome_atr": outcome_atr,
            "atr_bars": int(atr_bars),
        })
        i = j + 1
    return events


def _period_bounds(ts: np.ndarray, rec: dict) -> dict[str, tuple[int, int]]:
    leg_start = int(rec["leg_start_ts"])
    effective = int(rec["effective_ts"])
    pre_i1 = int(np.searchsorted(ts, leg_start, side="left"))
    fit_i0 = int(np.searchsorted(ts, leg_start, side="left"))
    fit_i1 = int(np.searchsorted(ts, effective, side="right"))
    return {
        "prefit": (0, pre_i1),
        "fitwin": (fit_i0, fit_i1),
    }


def build_line_events(
    result_df: pd.DataFrame,
    klines_df: pd.DataFrame,
    compute_id: str = "",
    symbol: str = "",
    interval: str = "",
    cfg: Any = None,
) -> pd.DataFrame:
    empty = pd.DataFrame(columns=EVENT_COLS)
    if result_df.empty or klines_df.empty:
        return empty

    params = resolve_event_params(cfg)
    kdf = klines_df.sort_values("ts").reset_index(drop=True)
    ts = kdf["ts"].to_numpy(dtype=np.int64)
    highs = kdf["high"].to_numpy(dtype=np.float64)
    lows = kdf["low"].to_numpy(dtype=np.float64)
    closes = kdf["close"].to_numpy(dtype=np.float64)
    atr = _atr_series(highs, lows, closes, int(params["event_atr_period"]))

    cache: dict[tuple, list[dict]] = {}
    out: list[dict] = []
    for rec in result_df.to_dict("records"):
        gid = fib_group_id(
            rec["effective_ts"], rec["multiplier"], rec["direction"],
            rec["leg_low"], rec["leg_high"],
        )
        bounds = _period_bounds(ts, rec)
        targets: list[tuple[str, Optional[float]]] = [("fib", float(rec["price"]))]
        pl = rec.get("nearest_cluster_center")
        if _has_price(pl):
            targets.append(("cluster", float(pl)))
        ident = {
            "compute_id": compute_id,
            "symbol": symbol,
            "interval": interval,
            "effective_ts": int(rec["effective_ts"]),
            "multiplier": int(rec["multiplier"]),
            "direction": str(rec["direction"]),
            "leg_low": float(rec["leg_low"]),
            "leg_high": float(rec["leg_high"]),
            "ratio": float(rec["ratio"]),
            "fib_group_id": gid,
        }
        for kind, price in targets:
            for period, (i0, i1) in bounds.items():
                key = (i0, i1, round(price, 2))
                if key not in cache:
                    cache[key] = _scan_period(
                        ts, highs, lows, closes, atr, i0, i1, price, params,
                    )
                for ev in cache[key]:
                    out.append({
                        **ident,
                        "target_kind": kind,
                        "target_price": round(price, 2),
                        "period": period,
                        "event_id": _event_id(gid, rec["ratio"], kind, period, ev["entered_ts"]),
                        **ev,
                    })
    if not out:
        return empty
    return pd.DataFrame(out, columns=EVENT_COLS)
