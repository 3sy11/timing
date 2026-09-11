"""从 result.parquet + klines 加工线级衍生表：bounce、consensus、同伴质量。"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

TOUCH_K = 0.001
RECENT_BARS = 200

_BOUNCE_KEYS = (
    "touch_bar_count", "bounce_from_above", "bounce_from_below",
    "bounce_count", "bounce_rate",
    "touch_bar_count_recent", "bounce_from_above_recent", "bounce_from_below_recent",
    "bounce_count_recent", "bounce_rate_recent",
)

_ID_COLS = [
    "effective_ts", "multiplier", "direction", "leg_low", "leg_high", "ratio", "price",
    "nearest_cluster_center", "nearest_cluster_conf", "is_cluster_aligned",
    "invalidated_ts", "fib_score",
]


def _group_key(row) -> tuple:
    return (int(row["effective_ts"]), int(row["multiplier"]), str(row["direction"]),
            round(float(row["leg_low"]), 4), round(float(row["leg_high"]), 4))


def _empty_bounce() -> dict:
    out = {k: 0 for k in _BOUNCE_KEYS if not k.endswith("rate") and not k.endswith("rate_recent")}
    out["bounce_rate"] = None
    out["bounce_rate_recent"] = None
    return out


def _rate(bounce, touch):
    if touch <= 0:
        return None
    return round(bounce / touch, 4)


def _bounce_counts(highs, lows, closes, i0: int, i1: int, price: float) -> dict:
    full = _scan_bounce(highs, lows, closes, i0, i1, price)
    n = i1 - i0
    recent = _scan_bounce(highs, lows, closes, i1 - RECENT_BARS, i1, price) if n > RECENT_BARS else full
    return {
        "touch_bar_count": full[0],
        "bounce_from_above": full[1],
        "bounce_from_below": full[2],
        "bounce_count": full[1] + full[2],
        "bounce_rate": _rate(full[1] + full[2], full[0]),
        "touch_bar_count_recent": recent[0],
        "bounce_from_above_recent": recent[1],
        "bounce_from_below_recent": recent[2],
        "bounce_count_recent": recent[1] + recent[2],
        "bounce_rate_recent": _rate(recent[1] + recent[2], recent[0]),
    }


def _scan_bounce(highs, lows, closes, i0: int, i1: int, price: float) -> tuple:
    if i1 <= i0:
        return 0, 0, 0
    r = price * TOUCH_K
    sl = slice(i0, i1)
    in_band = (lows[sl] <= price + r) & (highs[sl] >= price - r)
    touch = int(in_band.sum())
    above = below = 0
    n = len(closes)
    for i in np.flatnonzero(in_band) + i0:
        if i == 0 or i + 1 >= n:
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


def _alive_at(effective_ts: int, invalidated_ts, t_end: int) -> bool:
    if int(effective_ts) > t_end:
        return False
    if invalidated_ts is None or (isinstance(invalidated_ts, float) and math.isnan(invalidated_ts)):
        return True
    return int(invalidated_ts) > t_end


def _prefix(d: dict, prefix: str) -> dict:
    return {f"{prefix}{k}": v for k, v in d.items()}


def _pick_representative(lines: list[dict], p: float) -> Optional[dict]:
    inner_aligned = [x for x in lines if x.get("is_cluster_aligned")
                     and 0.001 < float(x["ratio"]) < 0.999]
    pool = inner_aligned or lines
    if not pool:
        return None
    return min(pool, key=lambda x: abs(float(x["price"]) - p))


def build_line_factors(result_df: pd.DataFrame, klines_df: pd.DataFrame) -> pd.DataFrame:
    if result_df.empty or klines_df.empty:
        return pd.DataFrame()

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

    bounce_by_price: dict[tuple, dict] = {}
    bounce_cache: dict[int, dict] = {}
    for key, idxs in by_group.items():
        rec0 = rows[idxs[0]]
        i0, i1 = _window_indices(ts, int(rec0["effective_ts"]), rec0.get("invalidated_ts"), last_ts)
        for idx in idxs:
            rec = rows[idx]
            fib_p = round(float(rec["price"]), 2)
            fk = (i0, i1, "f", fib_p)
            if fk not in bounce_by_price:
                bounce_by_price[fk] = (
                    _bounce_counts(highs, lows, closes, i0, i1, float(rec["price"]))
                    if i1 > i0 else _empty_bounce()
                )
            pl = rec.get("nearest_cluster_center")
            has_pl = pl is not None and not (isinstance(pl, float) and math.isnan(pl))
            if not has_pl:
                pl_b = {k: None for k in _BOUNCE_KEYS}
            else:
                pk = (i0, i1, "p", round(float(pl), 2))
                if pk not in bounce_by_price:
                    bounce_by_price[pk] = (
                        _bounce_counts(highs, lows, closes, i0, i1, float(pl))
                        if i1 > i0 else _empty_bounce()
                    )
                pl_b = bounce_by_price[pk]
            bounce_cache[idx] = {**_prefix(bounce_by_price[fk], "fib_"), **_prefix(pl_b, "pl_")}

    group_meta = []
    for key, idxs in by_group.items():
        sample = rows[idxs[0]]
        group_meta.append({
            "key": key,
            "effective_ts": int(sample["effective_ts"]),
            "invalidated_ts": sample.get("invalidated_ts"),
            "multiplier": int(sample["multiplier"]),
            "direction": str(sample["direction"]),
            "lines": [rows[i] for i in idxs],
        })

    living_cache: dict[int, list] = {}
    out_rows = []
    for idx, rec in enumerate(rows):
        t_end = _t_end(rec.get("invalidated_ts"), last_ts)
        if t_end not in living_cache:
            living_cache[t_end] = [
                g for g in group_meta
                if _alive_at(g["effective_ts"], g["invalidated_ts"], t_end)
            ]
        living = living_cache[t_end]
        self_key = _group_key(rec)
        p_fib = float(rec["price"])
        p_pl = rec.get("nearest_cluster_center")
        has_pl = p_pl is not None and not (isinstance(p_pl, float) and math.isnan(p_pl))
        fib_cons = _consensus_block(living, self_key, p_fib, "fib", bounce_cache)
        pl_cons = (
            _consensus_block(living, self_key, float(p_pl), "pl", bounce_cache)
            if has_pl else _null_consensus("pl")
        )
        ident = {c: rec[c] for c in _ID_COLS if c in rec}
        ident["t_end"] = t_end
        out_rows.append({**ident, **bounce_cache[idx], **fib_cons, **pl_cons})
    return pd.DataFrame(out_rows)


def _null_consensus(kind: str) -> dict:
    p = f"{kind}_"
    return {
        f"{p}consensus_group_count": None,
        f"{p}consensus_multiplier_count": None,
        f"{p}consensus_direction_count": None,
        f"{p}consensus_line_count": None,
        f"{p}peer_bounce_rate_mean": None,
        f"{p}peer_bounce_rate_min": None,
        f"{p}peer_aligned_ratio": None,
        f"{p}peer_conf_sum": None,
    }


def _consensus_block(living, self_key, p: float, kind: str, bounce_cache: dict) -> dict:
    r = p * TOUCH_K
    price_field = "price" if kind == "fib" else "nearest_cluster_center"
    rate_field = f"{kind}_bounce_rate_recent"
    matched = []
    line_count = 0
    self_lines = []
    for g in living:
        if g["key"] == self_key:
            self_lines = g["lines"]
            continue
        hit = []
        for ln in g["lines"]:
            q = ln.get(price_field)
            if q is None or (isinstance(q, float) and math.isnan(q)):
                continue
            if abs(float(q) - p) <= r:
                hit.append(ln)
        if hit:
            line_count += len(hit)
            matched.append((g, hit))

    self_in_band = 0
    for ln in self_lines:
        q = ln.get(price_field)
        if q is None or (isinstance(q, float) and math.isnan(q)):
            continue
        if abs(float(q) - p) <= r:
            self_in_band += 1
    line_count += max(self_in_band, 1)

    peer_rates, aligned, confs = [], [], []
    for g, hit in matched:
        rep = _pick_representative(hit, p)
        if rep is None:
            continue
        bounce_row = bounce_cache.get(rep["_idx"])
        rate = bounce_row.get(rate_field) if bounce_row else None
        if rate is not None and not (isinstance(rate, float) and math.isnan(rate)):
            peer_rates.append(float(rate))
        aligned.append(1.0 if rep.get("is_cluster_aligned") else 0.0)
        conf = rep.get("nearest_cluster_conf")
        if conf is not None and not (isinstance(conf, float) and math.isnan(conf)):
            confs.append(float(conf))

    prefix = f"{kind}_"
    return {
        f"{prefix}consensus_group_count": 1 + len(matched),
        f"{prefix}consensus_multiplier_count": len({self_key[1]} | {g["multiplier"] for g, _ in matched}),
        f"{prefix}consensus_direction_count": len({self_key[2]} | {g["direction"] for g, _ in matched}),
        f"{prefix}consensus_line_count": line_count,
        f"{prefix}peer_bounce_rate_mean": round(sum(peer_rates) / len(peer_rates), 4) if peer_rates else None,
        f"{prefix}peer_bounce_rate_min": round(min(peer_rates), 4) if peer_rates else None,
        f"{prefix}peer_aligned_ratio": round(sum(aligned) / len(aligned), 4) if aligned else None,
        f"{prefix}peer_conf_sum": round(sum(confs), 4) if confs else None,
    }
