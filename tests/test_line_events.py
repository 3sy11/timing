import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computation.algo.fib_retracement.line_events import build_line_events, fib_group_id

_OLD = {"event_atr_band_k": 0.0}


def _klines(rows):
    return pd.DataFrame(rows)


def _line(**kwargs):
    base = {
        "effective_ts": 6, "multiplier": 1, "direction": "up", "fib_score": 2.0,
        "leg_low": 90.0, "leg_high": 110.0, "leg_start_ts": 4, "leg_end_ts": 6,
        "invalidated_ts": None, "ratio": 0.5, "price": 100.0,
        "nearest_cluster_center": 100.0, "nearest_cluster_conf": 1.5,
        "is_cluster_aligned": True,
    }
    base.update(kwargs)
    return base


def test_consecutive_in_band_is_one_event():
    klines = _klines([
        {"ts": 0, "high": 103, "low": 102, "close": 102.5},
        {"ts": 1, "high": 101, "low": 99.9, "close": 100.2},
        {"ts": 2, "high": 100.3, "low": 99.8, "close": 100.1},
        {"ts": 3, "high": 102, "low": 101, "close": 101.5},
    ])
    rec = _line(leg_start_ts=4, effective_ts=4)
    df = build_line_events(pd.DataFrame([rec]), klines, cfg=_OLD)
    pre = df[(df["period"] == "prefit") & (df["target_kind"] == "fib")]
    assert len(pre) == 1
    assert pre.iloc[0]["touch_bar_count"] == 2
    assert pre.iloc[0]["entered_ts"] == 1
    assert pre.iloc[0]["outcome"] == "bounce"
    assert pre.iloc[0]["approach"] == "from_above"


def test_incomplete_at_period_end_not_stored():
    klines = _klines([
        {"ts": 0, "high": 103, "low": 102, "close": 102.5},
        {"ts": 1, "high": 101, "low": 99.9, "close": 100.1},
        {"ts": 2, "high": 100.2, "low": 99.8, "close": 100.0},
    ])
    rec = _line(leg_start_ts=3, effective_ts=3)
    df = build_line_events(pd.DataFrame([rec]), klines, cfg=_OLD)
    pre = df[(df["period"] == "prefit") & (df["target_kind"] == "fib")]
    assert pre.empty


def test_period_split_prefit_vs_fitwin():
    klines = _klines([
        {"ts": 0, "high": 103, "low": 102, "close": 102.5},
        {"ts": 1, "high": 101, "low": 99.9, "close": 100.2},
        {"ts": 2, "high": 102, "low": 101, "close": 101.5},
        {"ts": 3, "high": 101, "low": 99.9, "close": 100.2},
        {"ts": 4, "high": 103, "low": 101.2, "close": 102.0},
        {"ts": 5, "high": 103, "low": 102, "close": 102.5},
    ])
    rec = _line(leg_start_ts=3, effective_ts=5, nearest_cluster_center=None)
    df = build_line_events(pd.DataFrame([rec]), klines, cfg=_OLD)
    assert set(df["period"]) == {"prefit", "fitwin"}
    assert (df["target_kind"] == "fib").all()
    pre = df[df["period"] == "prefit"].iloc[0]
    fit = df[df["period"] == "fitwin"].iloc[0]
    assert pre["entered_ts"] == 1
    assert fit["entered_ts"] == 3
    assert pre["outcome"] == "bounce"
    assert fit["outcome"] == "bounce"
    assert fit["approach"] == "from_above"


def test_no_cluster_skips_cluster_rows():
    klines = _klines([
        {"ts": 0, "high": 103, "low": 102, "close": 102.5},
        {"ts": 1, "high": 101, "low": 99.9, "close": 100.2},
        {"ts": 2, "high": 102, "low": 101, "close": 101.5},
    ])
    rec = _line(leg_start_ts=3, effective_ts=3, nearest_cluster_center=float("nan"))
    df = build_line_events(pd.DataFrame([rec]), klines, cfg=_OLD)
    assert (df["target_kind"] == "fib").all()


def test_same_group_shares_fib_group_id():
    a = _line(ratio=0.382, price=100.0, nearest_cluster_center=None)
    b = _line(ratio=0.618, price=105.0, nearest_cluster_center=None)
    assert fib_group_id(a["effective_ts"], a["multiplier"], a["direction"], a["leg_low"], a["leg_high"]) == \
        fib_group_id(b["effective_ts"], b["multiplier"], b["direction"], b["leg_low"], b["leg_high"])
    klines = _klines([
        {"ts": 0, "high": 103, "low": 102, "close": 102.5},
        {"ts": 1, "high": 101, "low": 99.9, "close": 100.2},
        {"ts": 2, "high": 102, "low": 101, "close": 101.5},
    ])
    a["leg_start_ts"] = 3
    a["effective_ts"] = 3
    b["leg_start_ts"] = 3
    b["effective_ts"] = 3
    df = build_line_events(pd.DataFrame([a, b]), klines, cfg=_OLD)
    ids = df["fib_group_id"].unique()
    assert len(ids) == 1


def _wide_klines_then(extra):
    rows = [{"ts": i, "high": 108, "low": 102, "close": 105} for i in range(16)]
    rows.extend(extra)
    return _klines(rows)


def test_old_bounce_can_be_atr_weak():
    klines = _wide_klines_then([
        {"ts": 16, "high": 106, "low": 104, "close": 105},
        {"ts": 17, "high": 100.3, "low": 99.8, "close": 100.1},
        {"ts": 18, "high": 101.2, "low": 100.3, "close": 101.0},
        {"ts": 19, "high": 101.3, "low": 100.4, "close": 101.1},
        {"ts": 20, "high": 101.2, "low": 100.3, "close": 101.0},
        {"ts": 21, "high": 101.1, "low": 100.2, "close": 100.8},
    ])
    rec = _line(leg_start_ts=22, effective_ts=22, nearest_cluster_center=None)
    df = build_line_events(pd.DataFrame([rec]), klines, cfg={**_OLD, "event_atr_period": 14})
    row = df[(df["period"] == "prefit") & (df["target_kind"] == "fib")].iloc[0]
    assert row["outcome"] == "bounce"
    assert row["outcome_atr"] == "weak"
    assert row["mfe_atr"] < 0.5


def test_atr_bounce_when_half_atr_favorable():
    klines = _wide_klines_then([
        {"ts": 16, "high": 106, "low": 104, "close": 105},
        {"ts": 17, "high": 100.3, "low": 99.8, "close": 100.1},
        {"ts": 18, "high": 106, "low": 100.2, "close": 105.5},
        {"ts": 19, "high": 106, "low": 104, "close": 105},
        {"ts": 20, "high": 106, "low": 104, "close": 105},
        {"ts": 21, "high": 106, "low": 104, "close": 105},
    ])
    rec = _line(leg_start_ts=22, effective_ts=22, nearest_cluster_center=None)
    df = build_line_events(pd.DataFrame([rec]), klines, cfg={**_OLD, "event_atr_period": 14})
    row = df[(df["period"] == "prefit") & (df["target_kind"] == "fib")].iloc[0]
    assert row["outcome"] == "bounce"
    assert row["outcome_atr"] == "bounce"
    assert row["mfe_atr"] >= 0.5
